import os
from functools import wraps
from io import BytesIO

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_file, session
from flask_cors import CORS

try:
    from PIL import Image
except Exception:  # Pillow is installed through requirements.txt, this keeps the app safer.
    Image = None


load_dotenv()

app = Flask(__name__)

# 1. 透過環境變數精準判斷是否為開發/除錯模式（預設為 True，方便本地直接跑）
IS_DEVELOPMENT = os.getenv("FLASK_DEBUG", "true").lower() in ["true", "1"]

app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY")
if not app.config["SECRET_KEY"] and not IS_DEVELOPMENT:
    raise ValueError("錯誤：正式環境必須在 .env 中設定 FLASK_SECRET_KEY！")
elif not app.config["SECRET_KEY"]:
    app.config["SECRET_KEY"] = "local-dev-fallback-key"

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = not IS_DEVELOPMENT  # 正式環境才強制 HTTPS

CORS(app, supports_credentials=True)

ORTHANC_URL = os.getenv("ORTHANC_URL", "http://127.0.0.1:8042").rstrip("/")
ORTHANC_USERNAME = os.getenv("ORTHANC_USERNAME", "")
ORTHANC_PASSWORD = os.getenv("ORTHANC_PASSWORD", "")
ORTHANC_VERIFY_SSL = os.getenv("ORTHANC_VERIFY_SSL", "true").lower() == "true"

DEMO_USERS = {
    "patient1": {"password": "1234", "role": "patient"},
    "doctor1": {"password": "1234", "role": "doctor"},
    "admin1": {"password": "1234", "role": "admin"},
}

ROLE_PERMISSIONS = {
    "patient": ["view", "modify", "delete"],
    "doctor": ["view", "upload"],
    "admin": ["view"],
}

ROLE_LABELS = {
    "patient": "patient",
    "doctor": "doctor",
    "admin": "admin",
}


# -------------------------
# Helper functions
# -------------------------
def orthanc_request(method: str, path: str, **kwargs):
    """Send a request to Orthanc."""
    url = f"{ORTHANC_URL}{path}"
    timeout = kwargs.pop("timeout", 30)

    headers = kwargs.pop("headers", {}) or {}
    if method.upper() == "GET":
        headers.setdefault("Accept", "image/png,image/jpeg,application/json,*/*")

    response = requests.request(
        method=method,
        url=url,
        auth=(ORTHANC_USERNAME, ORTHANC_PASSWORD),
        timeout=timeout,
        headers=headers,
        verify=ORTHANC_VERIFY_SSL,  # <--- 改用環境變數控制
        **kwargs,
    )
    response.raise_for_status()
    return response


def get_current_user():
    return session.get("user")


def get_permissions(role: str):
    return ROLE_PERMISSIONS.get(role, [])


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return jsonify({"ok": False, "message": "尚未登入"}), 401
        return func(*args, **kwargs)

    return wrapper


def permission_required(permission: str):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({"ok": False, "message": "尚未登入"}), 401

            role = user.get("role", "")
            if permission not in get_permissions(role):
                return jsonify({"ok": False, "message": f"{role} 沒有 {permission} 權限"}), 403

            return func(*args, **kwargs)

        return wrapper

    return decorator


def dicom_tags(resource: dict, key: str):
    return resource.get(key, {}) or {}


def convert_to_png_bytes(content: bytes):
    """
    Convert Orthanc image response to PNG.
    This helps when Orthanc returns non-browser-friendly image formats.
    """
    if Image is None:
        return None

    try:
        image = Image.open(BytesIO(content))
        image.load()

        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")

        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()
    except Exception:
        return None


def make_image_response(content: bytes, source_path: str):
    """
    Return image response.
    Prefer converting to PNG to avoid browser preview failures.
    """
    png_content = convert_to_png_bytes(content)
    if png_content:
        response = Response(png_content, mimetype="image/png")
    else:
        response = Response(content, mimetype="image/png")

    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["X-Preview-Source"] = source_path
    return response


# -------------------------
# Page
# -------------------------
@app.route("/")
def index():
    return render_template("index.html")


# -------------------------
# Auth APIs
# -------------------------
@app.route("/api/health", methods=["GET"])
def health_check():
    status = {"backend": "ok", "orthanc": "unknown"}
    orthanc_error = None

    try:
        resp = orthanc_request("GET", "/system")
        if resp.status_code == 200:
            status["orthanc"] = "ok"
    except Exception as exc:
        status["orthanc"] = "error"
        orthanc_error = str(exc)

    return jsonify({"ok": True, "status": status, "orthanc_error": orthanc_error})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()

    user = DEMO_USERS.get(username)
    if not user:
        return jsonify({"ok": False, "message": "帳號不存在"}), 401

    if user["password"] != password:
        return jsonify({"ok": False, "message": "密碼錯誤"}), 401

    session["user"] = {"username": username, "role": user["role"]}
    return jsonify(
        {
            "ok": True,
            "message": "登入成功",
            "user": session["user"],
            "permissions": get_permissions(user["role"]),
            "role_label": ROLE_LABELS.get(user["role"], user["role"]),
        }
    )


@app.route("/api/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return jsonify({"ok": True, "message": "已登出"})


@app.route("/api/me", methods=["GET"])
def me():
    user = get_current_user()
    if not user:
        return jsonify({"ok": False, "user": None}), 401

    role = user.get("role", "")
    return jsonify(
        {
            "ok": True,
            "user": user,
            "permissions": get_permissions(role),
            "role_label": ROLE_LABELS.get(role, role),
        }
    )


# -------------------------
# Orthanc read APIs
# -------------------------
@app.route("/api/studies", methods=["GET"])
@login_required
@permission_required("view")
def get_studies():
    try:
        study_ids = orthanc_request("GET", "/studies").json()
        studies = []

        for study_id in study_ids:
            detail = orthanc_request("GET", f"/studies/{study_id}").json()
            main_tags = dicom_tags(detail, "MainDicomTags")
            patient_tags = dicom_tags(detail, "PatientMainDicomTags")
            series_ids = detail.get("Series", []) or []

            instances_count = 0
            for series_id in series_ids:
                series_detail = orthanc_request("GET", f"/series/{series_id}").json()
                instances_count += len(series_detail.get("Instances", []) or [])

            studies.append(
                {
                    "id": study_id,
                    "study_instance_uid": main_tags.get("StudyInstanceUID", ""),
                    "study_date": main_tags.get("StudyDate", ""),
                    "study_description": main_tags.get("StudyDescription", ""),
                    "accession_number": main_tags.get("AccessionNumber", ""),
                    "patient_name": patient_tags.get("PatientName", ""),
                    "patient_id": patient_tags.get("PatientID", ""),
                    "instances_count": instances_count,
                    "series_count": len(series_ids),
                }
            )

        studies.sort(key=lambda item: item.get("study_date", ""), reverse=True)
        return jsonify({"ok": True, "data": studies})

    except requests.RequestException as exc:
        return jsonify({"ok": False, "message": f"Orthanc 讀取失敗：{exc}"}), 500


@app.route("/api/studies/<study_id>", methods=["GET"])
@login_required
@permission_required("view")
def get_study_detail(study_id):
    try:
        detail = orthanc_request("GET", f"/studies/{study_id}").json()
        main_tags = dicom_tags(detail, "MainDicomTags")
        patient_tags = dicom_tags(detail, "PatientMainDicomTags")
        series_ids = detail.get("Series", []) or []

        instances = []

        for series_id in series_ids:
            series_detail = orthanc_request("GET", f"/series/{series_id}").json()
            series_tags = dicom_tags(series_detail, "MainDicomTags")

            for instance_id in series_detail.get("Instances", []) or []:
                ins_detail = orthanc_request("GET", f"/instances/{instance_id}").json()
                tags = dicom_tags(ins_detail, "MainDicomTags")

                instances.append(
                    {
                        "id": instance_id,
                        "series_id": series_id,
                        "series_description": series_tags.get("SeriesDescription", ""),
                        "series_number": series_tags.get("SeriesNumber", ""),
                        "sop_instance_uid": tags.get("SOPInstanceUID", ""),
                        "instance_number": tags.get("InstanceNumber", ""),
                        "modality": tags.get("Modality", ""),
                        "content_date": tags.get("ContentDate", ""),
                        "content_time": tags.get("ContentTime", ""),
                    }
                )

        instances.sort(key=lambda item: str(item.get("series_number", "")) + "-" + str(item.get("instance_number", "")))

        study_info = {
            "id": study_id,
            "patient_name": patient_tags.get("PatientName", ""),
            "patient_id": patient_tags.get("PatientID", ""),
            "study_date": main_tags.get("StudyDate", ""),
            "study_description": main_tags.get("StudyDescription", ""),
            "study_instance_uid": main_tags.get("StudyInstanceUID", ""),
            "instances_count": len(instances),
        }

        return jsonify({"ok": True, "study": study_info, "instances": instances})

    except requests.RequestException as exc:
        return jsonify({"ok": False, "message": f"讀取 Study 詳細資料失敗：{exc}"}), 500


@app.route("/api/instances/<instance_id>/preview", methods=["GET"])
@login_required
@permission_required("view")
def get_instance_preview(instance_id):
    """
    Robust DICOM preview endpoint.

    It tries several Orthanc preview/image endpoints.
    If Orthanc returns an image format that the browser may not display,
    Pillow converts it to PNG before sending it to the frontend.
    """
    preview_paths = [
        f"/instances/{instance_id}/preview",
        f"/instances/{instance_id}/frames/0/preview",
        f"/instances/{instance_id}/image-uint8",
        f"/instances/{instance_id}/rendered",
        f"/instances/{instance_id}/frames/0/rendered",
    ]

    errors = []

    for path in preview_paths:
        try:
            resp = orthanc_request("GET", path, timeout=60)
            if not resp.content:
                errors.append(f"{path}：空白內容")
                continue

            image_response = make_image_response(resp.content, path)
            return image_response

        except requests.RequestException as exc:
            errors.append(f"{path}：{exc}")

    return jsonify(
        {
            "ok": False,
            "message": "此 DICOM 目前無法預覽，可能是 Orthanc 無法產生預覽圖、影像格式不支援，或該 instance 沒有可顯示的像素資料。",
            "attempts": errors,
        }
    ), 500


@app.route("/api/instances/<instance_id>/download", methods=["GET"])
@login_required
@permission_required("view")
def download_instance(instance_id):
    try:
        resp = orthanc_request("GET", f"/instances/{instance_id}/file", timeout=60)
        return send_file(
            BytesIO(resp.content),
            mimetype="application/dicom",
            as_attachment=True,
            download_name=f"{instance_id}.dcm",
        )
    except requests.RequestException as exc:
        return jsonify({"ok": False, "message": f"DICOM 下載失敗：{exc}"}), 500


# -------------------------
# Role-based write APIs
# -------------------------
@app.route("/api/instances/upload", methods=["POST"])
@login_required
@permission_required("upload")
def upload_instance():
    if "dicom" not in request.files:
        return jsonify({"ok": False, "message": "請先選擇 DICOM 檔案"}), 400

    file = request.files["dicom"]
    if not file or not file.filename:
        return jsonify({"ok": False, "message": "請先選擇 DICOM 檔案"}), 400

    try:
        uploaded = orthanc_request(
            "POST",
            "/instances",
            data=file.read(),
            headers={"Content-Type": "application/dicom"},
            timeout=60,
        ).json()

        return jsonify({"ok": True, "message": "DICOM 加入成功", "orthanc": uploaded})

    except requests.RequestException as exc:
        return jsonify({"ok": False, "message": f"DICOM 加入失敗：{exc}"}), 500


@app.route("/api/instances/<instance_id>/modify", methods=["POST"])
@login_required
@permission_required("modify")
def modify_instance(instance_id):
    data = request.get_json(silent=True) or {}

    replace = {}
    patient_name = (data.get("patient_name") or "").strip()
    study_description = (data.get("study_description") or "").strip()

    if patient_name:
        replace["PatientName"] = patient_name

    if study_description:
        replace["StudyDescription"] = study_description

    if not replace:
        return jsonify({"ok": False, "message": "至少要填一個欄位才能修改"}), 400

    payload = {
        "Replace": replace,
        "RemovePrivateTags": bool(data.get("remove_private_tags", False)),
    }

    try:
        modified_file = orthanc_request(
            "POST",
            f"/instances/{instance_id}/modify",
            json=payload,
            timeout=60,
        ).content

        uploaded = orthanc_request(
            "POST",
            "/instances",
            data=modified_file,
            headers={"Content-Type": "application/dicom"},
            timeout=60,
        ).json()

        deleted_original = False
        if data.get("replace_original", True):
            orthanc_request("DELETE", f"/instances/{instance_id}", timeout=60)
            deleted_original = True

        return jsonify(
            {
                "ok": True,
                "message": "DICOM 修改成功",
                "new_resource": uploaded,
                "deleted_original": deleted_original,
            }
        )

    except requests.RequestException as exc:
        return jsonify({"ok": False, "message": f"DICOM 修改失敗：{exc}"}), 500


@app.route("/api/instances/<instance_id>", methods=["DELETE"])
@login_required
@permission_required("delete")
def delete_instance(instance_id):
    try:
        orthanc_request("DELETE", f"/instances/{instance_id}", timeout=60)
        return jsonify({"ok": True, "message": "DICOM 已移除"})
    except requests.RequestException as exc:
        return jsonify({"ok": False, "message": f"DICOM 移除失敗：{exc}"}), 500


if __name__ == "__main__":
    is_debug_mode = os.getenv("FLASK_DEBUG", "true").lower() in ["true", "1"]
    app.run(debug=is_debug_mode, host="0.0.0.0", port=5000)