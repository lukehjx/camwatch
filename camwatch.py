#!/usr/bin/env python3
"""
办公室摄像头智能巡查系统 v3.0
- 多摄像头支持（config.json）
- 截帧失败重试（最多3次）
- 摄像头离线检测（ping）
- 腾讯云数据万象结构化检测（DetectBody + DetectLabel + ImageQuality）
- Claude Sonnet 语义总结
- AI 结果字段校验
- 截图上传腾讯云 COS
- 企微推送含图片
- 工作日/周末区分
"""
import subprocess
import base64
import json
import os
import sys
import time
import logging
import shutil
import sqlite3
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, date
try:
    from wifi_probe import get_presence_hint as _wifi_hint
    _WIFI_PROBE_OK = True
except ImportError:
    _WIFI_PROBE_OK = False
    def _wifi_hint(use_cache=True): return ""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/root/camwatch/camwatch.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ─── 路径 ────────────────────────────────────────────────────────────────────
BASE_DIR = "/root/camwatch"
CONFIG_PATH = f"{BASE_DIR}/config.json"
DB_PATH = f"{BASE_DIR}/camwatch.db"
SNAPSHOT_DIR = f"{BASE_DIR}/snapshots"

# ─── 加载配置 ────────────────────────────────────────────────────────────────
# 摄像头场景提示
NOTICE = (
    "【画面说明】\n"
    "1. 判断室内灯光是否开启：只看天花板灯具是否亮起。\n"
    "2. 判断设备是否开启：屏幕有内容显示才算开启，设备待机/屏保也算开启，不要因为屏幕变暗就判断为关闭。\n"
    "3. 判断人员：只有明确看到人才算有人，不要推测。\n\n"
)

def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


# ─── 摄像头离线检测 ──────────────────────────────────────────────────────────
def ping_camera(ip: str) -> bool:
    try:
        result = subprocess.run(
            ["ping", "-c", "2", "-W", "2", ip],
            capture_output=True, timeout=8
        )
        return result.returncode == 0
    except Exception as e:
        log.warning(f"ping 异常: {e}")
        return False


# ─── 截帧（含重试）──────────────────────────────────────────────────────────
def capture_frame(rtsp_url: str, snapshot_path: str, retries: int = 3) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-vframes", "1",
        "-q:v", "2",
        snapshot_path,
    ]
    for attempt in range(1, retries + 1):
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=20)
            if result.returncode == 0 and os.path.exists(snapshot_path):
                size = os.path.getsize(snapshot_path)
                if size > 1000:
                    log.info(f"截帧成功（第{attempt}次），文件大小 {size} bytes")
                    return True
                log.warning(f"截帧文件过小（{size} bytes），第{attempt}次")
            else:
                log.warning(f"截帧失败（第{attempt}次）: {result.stderr.decode()[-200:]}")
        except subprocess.TimeoutExpired:
            log.warning(f"截帧超时（第{attempt}次）")
        except Exception as e:
            log.warning(f"截帧异常（第{attempt}次）: {e}")
        if attempt < retries:
            time.sleep(5)
    log.error(f"截帧全部失败，共重试 {retries} 次")
    return False


# ─── COS 上传 ────────────────────────────────────────────────────────────────
def upload_to_cos(local_path: str, cos_key: str, cfg: dict):
    """上传文件到COS，返回 (cos_url, cos_client, cos_key) 三元组"""
    try:
        from qcloud_cos import CosConfig, CosS3Client
        config = CosConfig(
            Region=cfg["region"],
            SecretId=cfg["secret_id"],
            SecretKey=cfg["secret_key"],
        )
        client = CosS3Client(config)
        client.upload_file(
            Bucket=cfg["bucket"],
            LocalFilePath=local_path,
            Key=cos_key,
            EnableMD5=False,
            ACL="public-read",
        )
        domain = cfg.get("domain")
        if domain:
            url = f"https://{domain}/{cos_key}"
        else:
            url = f"https://{cfg['bucket']}.cos.{cfg['region']}.myqcloud.com/{cos_key}"
        log.info(f"COS 上传成功: {url}")
        return url, client, cos_key
    except Exception as e:
        log.error(f"COS 上传失败: {e}")
        return None, None, None


# ─── 数据万象 CI 检测 ────────────────────────────────────────────────────────
def ci_detect_body(client, bucket: str, cos_key: str) -> int:
    """人体检测，返回检测到的人数"""
    try:
        # 尝试 SDK 方法
        if hasattr(client, 'ci_detect_body'):
            resp = client.ci_detect_body(Bucket=bucket, Key=cos_key)
            body_info = resp.get('Body', {}).get('BodyInfos', {}).get('BodyInfo', [])
            if isinstance(body_info, dict):
                body_info = [body_info]
            count = len(body_info)
            log.info(f"[CI] DetectBody SDK: 检测到 {count} 人")
            return count

        # 回退：预签名 URL
        # 使用正确的参数名 AIBodyRecognition
        url = client.get_presigned_url(
            Method='GET', Bucket=bucket, Key=cos_key,
            Params={'ci-process': 'AIBodyRecognition'}, Expired=300
        )
        raw = urllib.request.urlopen(url, timeout=30).read()
        log.info(f"[CI] AIBodyRecognition raw: {raw[:300]}")
        root = ET.fromstring(raw)
        status = root.findtext('Status')
        if status == '0':
            log.info("[CI] AIBodyRecognition: 未检测到人体")
            return 0
        bodies = root.findall('.//Body') or root.findall('.//BodyInfo')
        count = len(bodies) if bodies else (1 if status == '1' else 0)
        log.info(f"[CI] AIBodyRecognition: 检测到 {count} 人")
        return count
    except Exception as e:
        log.warning(f"[CI] DetectBody 失败（graceful fallback）: {e}")
        return -1  # -1 表示检测失败


def ci_detect_label(client, bucket: str, cos_key: str) -> list:
    """图像标签检测，返回标签名称列表"""
    try:
        if hasattr(client, 'ci_detect_label'):
            resp = client.ci_detect_label(Bucket=bucket, Key=cos_key)
            labels_raw = resp.get('Labels', [])
            if isinstance(labels_raw, dict):
                labels_raw = [labels_raw]
            labels = [item.get('Name', '') for item in labels_raw if item.get('Name')]
            log.info(f"[CI] DetectLabel SDK: {labels}")
            return labels

        url = client.get_presigned_url(
            Method='GET',
            Bucket=bucket,
            Key=cos_key,
            Params={'ci-process': 'detect-label'},
            Expired=300
        )
        raw = urllib.request.urlopen(url, timeout=30).read()
        log.info(f"[CI] DetectLabel raw XML (first 500): {raw[:500]}")
        root = ET.fromstring(raw)
        # 只取置信度 >= 25 的标签，过滤噪音
        pairs = []
        for lbl in root.findall('Labels'):
            name_el = lbl.find('Name')
            conf_el = lbl.find('Confidence')
            if name_el is not None and conf_el is not None:
                conf = int(conf_el.text)
                if conf >= 25:
                    pairs.append(name_el.text)
        labels = pairs
        log.info(f"[CI] DetectLabel presigned (>=25): {labels}")
        return labels
    except Exception as e:
        log.warning(f"[CI] DetectLabel 失败（graceful fallback）: {e}")
        return []


def ci_image_quality(client, bucket: str, cos_key: str) -> int:
    """图像质量检测，返回亮度值（0-100，-1表示失败）"""
    try:
        if hasattr(client, 'ci_get_image_quality'):
            resp = client.ci_get_image_quality(Bucket=bucket, Key=cos_key)
            brightness = resp.get('Brightness', -1)
            log.info(f"[CI] ImageQuality SDK: Brightness={brightness}")
            return int(brightness) if brightness != -1 else -1

        resp = client.ci_image_assess_quality(Bucket=bucket, Key=cos_key)
        clarity = int(resp.get('ClarityScore', -1))
        log.info(f"[CI] AssessQuality: ClarityScore={clarity}")
        return clarity
    except Exception as e:
        log.warning(f"[CI] ImageQuality 失败（graceful fallback）: {e}")
        return -1


# ─── AI 分析（CI + Claude Sonnet 组合）────────────────────────────────────────
def analyze_image(image_path: str, ai_cfg: dict, cos_key: str = None, cos_client=None, cam_notice=None) -> dict:
    """
    先调用腾讯云 CI 接口获取结构化检测数据，
    再将结构化数据 + 图片一起发给 Claude 做语义总结。
    CI 接口失败时降级为纯 Claude 分析。
    """
    # ── 1. 腾讯云 CI 结构化检测 ──────────────────────────────────────────────
    body_count = -1
    labels = []
    brightness = -1
    ci_available = False

    use_ci = ai_cfg.get("use_ci", False)
    if use_ci and cos_key and cos_client:
        try:
            # 从 cos_client 获取 bucket（通过 config）
            cos_cfg = _get_cos_cfg()
            bucket = cos_cfg.get("bucket", "")
            if bucket:
                log.info("[CI] 开始数据万象结构化检测...")
                body_count = ci_detect_body(cos_client, bucket, cos_key)
                labels = ci_detect_label(cos_client, bucket, cos_key)
                brightness = ci_image_quality(cos_client, bucket, cos_key)
                ci_available = True
                log.info(f"[CI] 检测完成: 人数={body_count}, 标签={labels}, 亮度={brightness}")
            else:
                log.warning("[CI] bucket 未配置，跳过 CI 检测")
        except Exception as e:
            log.warning(f"[CI] 整体检测失败，降级为纯 Claude 分析: {e}")

    # ── 2. 构建 Prompt（含结构化数据）────────────────────────────────────────
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    if ci_available:
        # 有 CI 数据时，先附上结构化信息
        body_desc = f"{body_count} 人" if body_count >= 0 else "检测失败"
        # 过滤可能触发平台敏感词的标签
        _filter_words = ["垃圾", "色情", "赌博", "毒品"]
        labels_clean = [lb for lb in labels if not any(fw in lb for fw in _filter_words)]
        labels_desc = ", ".join(labels_clean) if labels_clean else "未检测到"
        brightness_desc = f"{brightness}/100（{'画面清晰，照明正常' if brightness > 50 else '画面偏暗'}）" if brightness >= 0 else "未检测"

        wifi_hint = _wifi_hint(use_cache=True) if _WIFI_PROBE_OK else ""
        wifi_section = f"{wifi_hint}\n\n" if wifi_hint else ""

        structured_info = f"""【腾讯云数据万象结构化检测结果】
- 人体检测人数: {body_desc}
- 场景标签: {labels_desc}
- 图像清晰度: {brightness_desc}

"""
        prompt_suffix = wifi_section + structured_info + (cam_notice or NOTICE) + """基于以上结构化检测数据和监控画面，请用JSON格式回答：
{
  "has_people": true/false,
  "people_count": 数字,
  "people_desc": "描述",
  "lights_on": true/false,
  "lights_desc": "描述",
  "devices_on": true/false,
  "devices_desc": "描述",
  "need_attention": true/false,
  "behavior": "一句话描述人员在做什么（如：有2人在工位前操作电脑；无人；1人站在走廊交谈）",
  "summary": "一句话总结"
}
只输出JSON，不要其他文字。"""
    else:
        # 降级：纯图片分析
        wifi_hint2 = _wifi_hint(use_cache=True) if _WIFI_PROBE_OK else ""
        wifi_section2 = f"{wifi_hint2}\n\n" if wifi_hint2 else ""
        prompt_suffix = wifi_section2 + (cam_notice or NOTICE) + """请分析这张办公室监控画面，用中文回答以下问题：

1. **人员情况**：画面中是否有人？有几人？在做什么？
2. **灯光状态**：办公室灯光是否开启？（亮/暗/部分开着）
3. **设备状态**：能看到的电脑、显示器、空调等是否处于开启状态？
4. **整体判断**：现在是否有人在加班？办公室是否需要关灯/关设备？

请用如下 JSON 格式回答（只输出 JSON，不要其他文字）：
{
  "has_people": true/false,
  "people_count": 数字,
  "people_desc": "描述",
  "lights_on": true/false,
  "lights_desc": "描述",
  "devices_on": true/false,
  "devices_desc": "描述",
  "need_attention": true/false,
  "behavior": "一句话描述人员在做什么（如：有2人在工位前操作电脑；无人；1人站在走廊交谈）",
  "summary": "一句话总结"
}"""

    # ── 3. 调用 Claude Sonnet ─────────────────────────────────────────────────
    model = ai_cfg.get("model", "anthropic/claude-sonnet-4-5")
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt_suffix},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        "max_tokens": 600,
        "temperature": 0.1,
    }

    req = urllib.request.Request(
        ai_cfg["endpoint"],
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {ai_cfg['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    raw_text = ""
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            raw_text = data["choices"][0]["message"]["content"].strip()
            # 提取 JSON
            text = raw_text
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            result = json.loads(text.strip())

            # 字段校验
            required = ["has_people", "lights_on", "devices_on", "need_attention", "summary"]
            missing = [f for f in required if f not in result]
            if missing:
                log.warning(f"AI 返回缺少字段: {missing}，原始: {raw_text[:200]}")
                for f in missing:
                    result[f] = None
                result["ai_warning"] = f"字段缺失: {missing}"

            # 附加 CI 元数据
            result["ci_body_count"] = body_count
            result["ci_labels"] = labels
            result["ci_brightness"] = brightness
            result["ci_used"] = ci_available

            log.info(f"[Claude] 分析完成（model={model}, ci_used={ci_available}）")
            return result

    except Exception as e:
        log.error(f"AI 分析失败: {e}，原始返回: {raw_text[:200]}")
        return {
            "has_people": None,
            "people_count": 0,
            "people_desc": "分析失败",
            "lights_on": None,
            "lights_desc": "分析失败",
            "devices_on": None,
            "devices_desc": "分析失败",
            "need_attention": False,
            "summary": f"AI 分析异常: {e}",
            "ai_warning": str(e),
            "ci_body_count": body_count,
            "ci_labels": labels,
            "ci_brightness": brightness,
            "ci_used": ci_available,
        }


# ─── 辅助：获取 COS 配置 ─────────────────────────────────────────────────────
_cos_cfg_cache = None
def _get_cos_cfg():
    global _cos_cfg_cache
    if _cos_cfg_cache is None:
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
            _cos_cfg_cache = cfg.get("cos", {})
        except Exception:
            _cos_cfg_cache = {}
    return _cos_cfg_cache


# ─── 企微推送 ─────────────────────────────────────────────────────────────────
def send_webhook(result: dict, snapshot_ok: bool, webhook_url: str,
                 camera_name: str = "办公室", cos_url: str = None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not snapshot_ok:
        if result.get("offline"):
            content = f"## ⚠️ 摄像头离线报警\n\n> 摄像头：**{camera_name}**\n> 时间：{now}\n\n摄像头网络不通，请检查设备状态。"
        else:
            content = f"## ⚠️ 截帧失败报警\n\n> 摄像头：**{camera_name}**\n> 时间：{now}\n\n截图失败，请检查摄像头或网络。"
    else:
        people_icon = "🚶 **有人**" if result.get("has_people") else "✅ **无人**"
        lights_icon = "💡 **灯光开启**" if result.get("lights_on") else "✅ **灯光关闭**"
        devices_icon = "🖥️ **设备运行**" if result.get("devices_on") else "✅ **设备关闭**"
        alert = "⚠️ **需要关注**" if result.get("need_attention") else "✅ **正常**"
        people_cnt = result.get("people_count", 0)
        people_str = f"（共 {people_cnt} 人）" if people_cnt and people_cnt > 0 else ""

        # CI 数据附加行
        ci_used = result.get("ci_used", False)
        ci_brightness = result.get("ci_brightness", -1)
        ci_labels = result.get("ci_labels", [])
        ci_body = result.get("ci_body_count", -1)
        ci_line = ""
        if ci_used:
            ci_line = f"\n> 🔍 CI检测: 人数={ci_body if ci_body >= 0 else '?'} | 亮度={ci_brightness if ci_brightness >= 0 else '?'} | 标签: {', '.join(ci_labels[:3]) if ci_labels else '无'}\n"

        content = f"""## 🏢 {camera_name} 巡查报告

> 🕐 巡查时间：**{now}**{ci_line}
---

### 📊 状态总览

| 检查项 | 状态 | 详情 |
|--------|------|------|
| 人员 | {people_icon}{people_str} | {result.get('people_desc', '-')} |
| 灯光 | {lights_icon} | {result.get('lights_desc', '-')} |
| 设备 | {devices_icon} | {result.get('devices_desc', '-')} |
| 综合 | {alert} | {result.get('summary', '-')} |
"""
        if cos_url:
            content += f"\n![监控截图]({cos_url})\n"
        content += "\n---\n*由旺财智能巡查系统 v3.0 自动发送*"

    def _send(payload_obj):
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload_obj).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_data = json.loads(resp.read())
                if resp_data.get("errcode") == 0:
                    log.info("Webhook 发送成功")
                    return True
                else:
                    log.error(f"Webhook 返回错误: {resp_data}")
                    return False
        except Exception as e:
            log.error(f"Webhook 发送失败: {e}")
            return False

    # 先发图片（直接显示在聊天中）
    if cos_url and snapshot_ok:
        import hashlib as _hashlib
        try:
            import urllib.parse as _up
            _encoded_url = _up.quote(cos_url, safe=":/?=&#@")
            img_data = urllib.request.urlopen(_encoded_url, timeout=15).read()
            img_md5 = _hashlib.md5(img_data).hexdigest()
            img_b64 = base64.b64encode(img_data).decode()
            _send({"msgtype": "image", "image": {"base64": img_b64, "md5": img_md5}})
        except Exception as e:
            log.warning(f"图片消息发送失败，跳过: {e}")

    # 再发文字报告（去掉末尾图片链接行，避免重复）
    content_no_img = content.split("\n![监控截图]")[0]
    _send({"msgtype": "markdown", "markdown": {"content": content_no_img}})


# ─── 数据库 ──────────────────────────────────────────────────────────────────
def init_db():
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        check_time DATETIME NOT NULL,
        camera_name TEXT DEFAULT '办公室',
        has_people BOOLEAN,
        people_count INTEGER DEFAULT 0,
        people_desc TEXT,
        lights_on BOOLEAN,
        lights_desc TEXT,
        devices_on BOOLEAN,
        devices_desc TEXT,
        need_attention BOOLEAN,
        summary TEXT,
        snapshot_path TEXT,
        cos_url TEXT,
        raw_result TEXT
    )""")
    conn.commit()
    conn.close()


def save_to_db(result: dict, snapshot_path: str, cos_url: str = None, camera_name: str = "办公室"):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("ALTER TABLE checks ADD COLUMN behavior TEXT")
        conn.commit()
    except:
        pass
    conn.execute(
        """INSERT INTO checks (check_time,camera_name,has_people,people_count,people_desc,
           lights_on,lights_desc,devices_on,devices_desc,need_attention,summary,
           snapshot_path,cos_url,raw_result,behavior)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            camera_name,
            result.get('has_people'),
            result.get('people_count', 0),
            result.get('people_desc', ''),
            result.get('lights_on'),
            result.get('lights_desc', ''),
            result.get('devices_on'),
            result.get('devices_desc', ''),
            result.get('need_attention'),
            result.get('summary', ''),
            snapshot_path,
            cos_url,
            json.dumps(result, ensure_ascii=False),
            result.get('behavior', ''),
        )
    )
    conn.commit()
    conn.close()



# --- 基线分析 ----------------------------------------------------------------
def get_baseline(cam_name: str, hour: int) -> dict:
    """计算指定摄像头在指定小时的历史基线"""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("""
            SELECT has_people, people_count, lights_on
            FROM checks
            WHERE camera_name=?
              AND CAST(strftime('%H', check_time) AS INTEGER) BETWEEN ? AND ?
            ORDER BY check_time DESC LIMIT 30
        """, (cam_name, max(0, hour-1), min(23, hour+1))).fetchall()
        conn.close()
        if len(rows) < 3:
            return {"insufficient_data": True}
        avg_people = sum(r[1] or 0 for r in rows) / len(rows)
        pct_lights = sum(1 for r in rows if r[2]) / len(rows)
        return {"avg_people": round(avg_people, 1), "pct_lights": round(pct_lights*100), "samples": len(rows)}
    except:
        return {}


# ─── 清理旧截图 ──────────────────────────────────────────────────────────────
def cleanup_old_snapshots(retention_days: int = 30):
    if not os.path.exists(SNAPSHOT_DIR):
        return
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for f in os.listdir(SNAPSHOT_DIR):
        fp = os.path.join(SNAPSHOT_DIR, f)
        if os.path.isfile(fp) and os.path.getmtime(fp) < cutoff:
            os.remove(fp)
            removed += 1
    if removed:
        log.info(f"清理旧截图 {removed} 张（>{retention_days}天）")


# ─── 单摄像头巡查 ────────────────────────────────────────────────────────────
def check_camera(camera: dict, cfg: dict, force: bool = False):
    cam_name = camera.get("name", "未知摄像头")
    rtsp = camera["rtsp"]
    cam_ip = camera.get("ip", "")
    webhook_url = camera["webhook"]
    cos_cfg = cfg.get("cos", {})
    ai_cfg = cfg.get("ai", {})

    log.info(f"=== 开始巡查：{cam_name} ===")

    # 摄像头离线检测
    if cam_ip:
        log.info(f"ping 检测摄像头 {cam_ip}...")
        if not ping_camera(cam_ip):
            log.error(f"摄像头 {cam_name} 离线（ping {cam_ip} 失败）")
            init_db()
            save_to_db({"offline": True, "summary": "摄像头离线"}, None, None, cam_name)
            return {
                "cam_name": cam_name,
                "result": {"offline": True, "summary": "摄像头离线"},
                "snapshot_ok": False, "cos_url": None, "tmp_path": None,
                "need_attention": True,
            }

    # 截帧
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_name = cam_name.replace(' ', '_')
    tmp_path = f"/tmp/camwatch_{safe_name}_{ts}.jpg"
    hist_path = f"{SNAPSHOT_DIR}/{safe_name}_{ts}.jpg"

    snapshot_ok = capture_frame(rtsp, tmp_path)

    cos_url = None
    cos_client = None
    cos_key_used = None

    if snapshot_ok:
        # 保存历史截图
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        shutil.copy2(tmp_path, hist_path)

        # 上传 COS，并保留 client 和 key 供 CI 使用
        if cos_cfg:
            prefix = cos_cfg.get("prefix", "camwatch/snapshots/")
            cos_key_used = f"{prefix}{safe_name}_{ts}.jpg"
            cos_url, cos_client, cos_key_used = upload_to_cos(tmp_path, cos_key_used, cos_cfg)

        # AI 分析（含 CI 结构化检测）
        log.info("正在分析画面...")
        result = analyze_image(
            tmp_path, ai_cfg,
            cos_key=cos_key_used,
            cos_client=cos_client,
            cam_notice=camera.get("notice")
        )
        log.info(f"分析结果: {result}")
    else:
        result = {"summary": "截帧失败"}
        hist_path = None

    # 写数据库
    try:
        init_db()
        save_to_db(result, hist_path, cos_url, cam_name)
    except Exception as e:
        log.warning(f"DB写入失败: {e}")

    # 功能3：基线对比
    try:
        _baseline = get_baseline(cam_name, datetime.now().hour)
        if not _baseline.get("insufficient_data") and _baseline.get("samples", 0) >= 3:
            _avg_p = _baseline.get("avg_people", 0)
            _cur_p = result.get("people_count", 0) or 0
            if _cur_p > _avg_p + 2:
                result["baseline_alert"] = f"今日人数({_cur_p})明显多于历史均值({_avg_p}人)"
            elif _avg_p > 1 and _cur_p == 0:
                result["baseline_alert"] = f"历史此时段通常有{_avg_p}人，今日无人"
    except:
        pass

    # 功能6：历史对比
    try:
        _conn2 = sqlite3.connect(DB_PATH)
        _last = _conn2.execute("""
            SELECT summary, has_people, lights_on, check_time
            FROM checks WHERE camera_name=?
            ORDER BY check_time DESC LIMIT 1
        """, (cam_name,)).fetchone()
        _conn2.close()
        if _last:
            result["last_summary"] = _last[0]
            result["last_checked_at"] = _last[3]
    except:
        pass

    # ── 业务规则覆盖 ─────────────────────────────────────────────────────────
    has_people  = result.get("has_people")
    lights_on   = result.get("lights_on")
    devices_on  = result.get("devices_on")

    # 规则1：无人但灯亮 → 异常（需要关灯）
    no_people_lights_on = (has_people is False) and (lights_on is True)
    # 规则2：无人但设备开着 → 异常（需要关设备）
    no_people_devices_on = (has_people is False) and (devices_on is True)

    # 功能9：夜间闯入检测
    _hour = datetime.now().hour
    _night_start = cfg.get("night_start_hour", 22)
    _night_end = cfg.get("night_end_hour", 6)
    _is_night = _hour >= _night_start or _hour < _night_end
    _intruder = _is_night and (has_people is True)
    if _intruder:
        result["night_alert"] = True
        result["attention_reason"] = (result.get("attention_reason", "") + " 🚨夜间有人").strip()

    need_attention = (
        bool(result.get("need_attention"))
        or not snapshot_ok
        or bool(result.get("offline"))
        or no_people_lights_on
        or no_people_devices_on
        or _intruder
    )

    # 更新 result 里的 need_attention（供汇总报告用）
    result["need_attention"] = need_attention
    if no_people_lights_on and not result.get("has_people"):
        result["attention_reason"] = "无人但灯亮"
    elif no_people_devices_on:
        result["attention_reason"] = "无人但设备开启"

    ci_body = result.get("ci_body_count", -1)
    if ci_body == 0 and result.get("has_people") is True:
        result["body_confidence"] = "low"
        old_reason = result.get("attention_reason", "")
        result["attention_reason"] = (old_reason + "（人员待确认）").strip()
    elif ci_body > 0 and result.get("has_people") is True:
        result["body_confidence"] = "high"

    log.info(f"=== 巡查完成：{cam_name} === need_attention={need_attention}")
    return {
        "cam_name": cam_name,
        "result": result,
        "snapshot_ok": snapshot_ok,
        "cos_url": cos_url,
        "tmp_path": tmp_path if snapshot_ok else None,
        "need_attention": need_attention,
    }



# --- 周度周报 ----------------------------------------------------------------
def send_weekly_report(cfg: dict):
    """生成并发送加班趋势周报"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT date(check_time) as day,
               SUM(CASE WHEN need_attention=1 THEN 1 ELSE 0 END) as anomalies,
               COUNT(*) as total,
               SUM(CASE WHEN has_people=1 THEN 1 ELSE 0 END) as with_people
        FROM checks
        WHERE check_time >= date('now', '-7 days')
        GROUP BY day ORDER BY day
    """).fetchall()
    conn.close()
    if not rows:
        log.info("周报：无数据")
        return
    webhook_url = cfg.get("webhook", "")
    lines = ["## 📊 周度加班巡查汇总\n"]
    for day, anom, total, with_ppl in rows:
        bar = "█" * min(anom, 10)
        lines.append(f"> {day}  异常{anom}次 {bar}  有人{with_ppl}次")
    payload = {"msgtype": "markdown", "markdown": {"content": "\n".join(lines)}}
    req = urllib.request.Request(webhook_url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
        log.info("周报发送成功")
    except Exception as e:
        log.error(f"周报发送失败: {e}")



# --- 能耗预警 ----------------------------------------------------------------
def check_energy_alert(cfg: dict, all_results: list):
    """统计本月能耗异常次数，超过阈值发预警"""
    threshold = cfg.get("energy_alert_threshold", 20)
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT camera_name, COUNT(*) as cnt
        FROM checks
        WHERE need_attention=1 AND lights_on=1 AND has_people=0
          AND check_time >= date('now', 'start of month')
        GROUP BY camera_name HAVING cnt >= ?
        ORDER BY cnt DESC
    """, (threshold,)).fetchall()
    conn.close()
    if not rows:
        return
    webhook_url = cfg.get("webhook", "")
    lines = [f"## ⚡ 能耗预警（本月无人亮灯）\n"]
    for name, cnt in rows:
        lines.append(f"> {name}：本月已发生 **{cnt}次** 无人亮灯")
    lines.append(f"\n> 阈值：{threshold}次")
    payload = {"msgtype": "markdown", "markdown": {"content": "\n".join(lines)}}
    req = urllib.request.Request(webhook_url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
        log.info("能耗预警发送成功")
    except Exception as e:
        log.warning(f"能耗预警发送失败: {e}")


# ─── 主入口 ──────────────────────────────────────────────────────────────────
def main():
    # 功能4：支持 weekly 参数
    if len(sys.argv) > 1 and sys.argv[1] == "weekly":
        cfg = load_config()
        send_weekly_report(cfg)
        return

    force = len(sys.argv) > 1 and sys.argv[1] == "test"
    cfg = load_config()

    # 周末跳过检测
    if not force:
        skip_weekend = cfg.get("schedule", {}).get("skip_weekend", False)
        if skip_weekend and date.today().weekday() >= 5:  # 5=周六, 6=周日
            log.info(f"今天是周末（{date.today().strftime('%A')}），跳过巡查")
            return

    # 清理旧截图
    retention = cfg.get("retention_days", 30)
    cleanup_old_snapshots(retention)

    # 巡查所有摄像头
    cameras = cfg.get("cameras", [])
    if not cameras:
        log.error("config.json 中没有配置摄像头")
        return

    all_results = []
    for camera in cameras:
        if not camera.get("enabled", True):
            continue
        try:
            r = check_camera(camera, cfg, force=force)
            if r:
                all_results.append(r)
        except Exception as e:
            log.error(f"摄像头 {camera.get('name')} 巡查异常: {e}")
            all_results.append({
                "cam_name": camera.get("name", "未知"),
                "result": {"summary": f"巡查异常: {e}"},
                "snapshot_ok": False, "cos_url": None, "tmp_path": None,
                "need_attention": True,
            })

    log.info("所有摄像头巡查完成")

    # ── 汇总发送：每次固定发一条（全景九宫格 + 状态总览）─────────────────────
    anomalies = [r for r in all_results if r.get("need_attention")]

    # 功能1：跨摄像头联动分析
    cross_result = ""
    if len(anomalies) >= 2:
        try:
            cam_summaries = "\n".join([
                f"- {r['cam_name']}: {r['result'].get('summary','')}"
                for r in all_results
            ])
            cross_prompt = f"以下是办公室各区域的巡查结果：\n{cam_summaries}\n\n请综合分析：1)哪些区域有关联异常？2)整体情况判断？3)建议行动？用2-3句话输出，中文，简洁。"
            cross_payload = {
                "model": cfg["ai"]["model"],
                "messages": [{"role": "user", "content": cross_prompt}],
                "max_tokens": 200, "temperature": 0.1
            }
            _req = urllib.request.Request(
                cfg["ai"]["endpoint"],
                data=json.dumps(cross_payload).encode(),
                headers={"Authorization": f"Bearer {cfg['ai']['api_key']}", "Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(_req, timeout=30) as _resp:
                cross_result = json.loads(_resp.read())["choices"][0]["message"]["content"].strip()
                log.info(f"跨摄像头联动分析: {cross_result}")
                open('/tmp/camwatch_cross_analysis.txt', 'w').write(cross_result)
        except Exception as e:
            log.warning(f"跨摄像头联动分析失败: {e}")
            cross_result = ""


    log.info(f"发现 {len(anomalies)} 路异常，准备汇总发送...")

    webhook_url = cfg.get("webhook", "")
    if not webhook_url:
        webhook_url = cameras[0].get("webhook", "") if cameras else ""
    if not webhook_url:
        log.error("未配置 webhook，无法发送汇总")
        return

    # 拼九宫格截图
    grid_path = None
    try:
        from PIL import Image, ImageDraw, ImageFont
        import math

        imgs_to_show = [r for r in all_results if r.get("tmp_path") and os.path.exists(r["tmp_path"])]
        imgs_to_show.sort(key=lambda r: 0 if r.get("need_attention") else 1)
        if imgs_to_show:
            COLS = min(3, len(imgs_to_show))
            ROWS = math.ceil(len(imgs_to_show) / COLS)
            TW, TH = 480, 270
            PAD = 6
            LH = 30
            W = COLS * TW + (COLS + 1) * PAD
            H = ROWS * (TH + LH) + (ROWS + 1) * PAD
            canvas = Image.new("RGB", (W, H), (18, 18, 18))
            draw = ImageDraw.Draw(canvas)
            font = None
            for fp in ["/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
                       "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]:
                if os.path.exists(fp):
                    font = ImageFont.truetype(fp, 16)
                    break
            font = font or ImageFont.load_default()

            for i, r in enumerate(imgs_to_show):
                row, col = divmod(i, COLS)
                x = PAD + col * (TW + PAD)
                y = PAD + row * (TH + LH + PAD)
                img = Image.open(r["tmp_path"]).convert("RGB").resize((TW, TH), Image.LANCZOS)
                canvas.paste(img, (x, y))
                draw.rectangle([x, y + TH, x + TW, y + TH + LH], fill=(0, 0, 0))
                color = (255, 90, 60) if r.get("need_attention") else (0, 210, 160)
                draw.text((x + 8, y + TH + 7), r["cam_name"], fill=color, font=font)

            grid_path = f"/tmp/camwatch_anomaly_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            # 右下角时间水印
            ts_wm = datetime.now().strftime("%Y-%m-%d %H:%M")
            wm_w, wm_h = 200, 28
            draw.rectangle([W - wm_w - 8, H - wm_h - 8, W - 8, H - 8], fill=(0, 0, 0))
            draw.text((W - wm_w - 4, H - wm_h - 5), ts_wm, fill=(200, 200, 200), font=font)
            canvas.save(grid_path, quality=88)
            log.info(f"异常九宫格已生成: {grid_path}")
    except Exception as e:
        log.warning(f"拼图失败，跳过: {e}")

    # 发图片
    if grid_path and os.path.exists(grid_path):
        try:
            import hashlib as _hlib, base64 as _b64
            img_data = open(grid_path, "rb").read()
            img_payload = {
                "msgtype": "image",
                "image": {
                    "base64": _b64.b64encode(img_data).decode(),
                    "md5": _hlib.md5(img_data).hexdigest(),
                },
            }
            req = urllib.request.Request(
                webhook_url,
                data=json.dumps(img_payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if json.loads(resp.read()).get("errcode") == 0:
                    log.info("汇总图片发送成功")
        except Exception as e:
            log.warning(f"汇总图片发送失败: {e}")

    # 发文字汇总
    ts_str = datetime.now().strftime("%m/%d %H:%M")
    total = len(all_results)
    anom_count = len(anomalies)

    if not anomalies:
        content = f"## ✅ {ts_str} 办公室巡查\n\n🟢 全部 {total} 路正常，办公室状态良好"
    else:
        lines = [f"## 📹 {ts_str} 办公室巡查报告\n"]
        lines.append(f"🔴 **异常（{anom_count}路）**")

        for idx, r in enumerate(anomalies):
            prefix = "└" if idx == len(anomalies) - 1 else "├"
            name = r["cam_name"]
            tags = []
            if r["result"].get("offline"):
                tags.append("📵离线")
            elif not r["snapshot_ok"]:
                tags.append("❌截帧失败")
            else:
                if r["result"].get("has_people"):
                    tags.append("👤有人")
                if r["result"].get("lights_on"):
                    tags.append("💡灯亮")
                if r["result"].get("devices_on"):
                    tags.append("🖥️设备开")
                conf = r["result"].get("body_confidence")
                if conf == "low":
                    tags.append("⚠️人员待确认")

            # 查持续次数
            try:
                import sqlite3 as _sq
                _conn = _sq.connect('/root/camwatch/camwatch.db')
                _rows = _conn.execute(
                    "SELECT need_attention FROM checks WHERE camera_name=? ORDER BY check_time DESC LIMIT 10",
                    (name,)
                ).fetchall()
                _streak = 0
                for _i, (_na,) in enumerate(_rows):
                    if _na and all(_rows[_j][0] for _j in range(_i)):
                        _streak += 1
                _conn.close()
                streak_str = f" ⚡已持续{_streak}次" if _streak >= 2 else ""
            except:
                streak_str = ""

            baseline_tag = ""
            if r["result"].get("baseline_alert"):
                baseline_tag = f"\n   > 📊 {r['result']['baseline_alert']}"
            night_tag = " 🚨夜间闯入" if r["result"].get("night_alert") else ""
            summary = r["result"].get("summary", "")[:30]
            tag_str = " ".join(tags)
            lines.append(f"{prefix} **{name}**  {tag_str}{streak_str}{night_tag}")
            if summary:
                lines.append(f"   > {summary}")
            if baseline_tag:
                lines.append(baseline_tag)
            last_s = r["result"].get("last_summary", "")
            if last_s:
                lines.append(f"   > 上次：{last_s[:20]}")

        normal_names = [r["cam_name"] for r in all_results if not r.get("need_attention")]
        if normal_names:
            lines.append(f"\n🟢 **正常（{total - anom_count}路）**")
            lines.append("   " + " · ".join(normal_names))

        content = "\n".join(lines)
    # 功能1：追加跨摄像头综合分析
    if cross_result:
        content += f"\n\n💡 **综合分析**\n> {cross_result}"

    # 缓存报告文本供 Web 预览
    try:
        open('/tmp/camwatch_last_report.txt', 'w').write(content)
    except: pass
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if json.loads(resp.read()).get("errcode") == 0:
                log.info("汇总报告发送成功")
    except Exception as e:
        log.error(f"汇总报告发送失败: {e}")

    # 功能5：能耗预警
    try:
        check_energy_alert(cfg, all_results)
    except Exception as e:
        log.warning(f"能耗预警检查失败: {e}")


if __name__ == "__main__":
    main()
