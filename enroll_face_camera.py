import os
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

import absl.logging
absl.logging.set_verbosity(absl.logging.ERROR)
absl.logging.use_absl_handler()

import warnings
warnings.filterwarnings('ignore')

import logging
logging.getLogger('tensorflow').setLevel(logging.ERROR)
logging.getLogger('absl').setLevel(logging.ERROR)

from deepface import DeepFace
import faiss
import numpy as np
import time
import json
import requests
import cv2


# ==============================
# CONFIG
# ==============================
BASE_URL = "https://entergym.onrender.com"
# BASE_URL = "http://127.0.0.1:8000/"
API_KEY = "mysecret123"

VIDEO_DURATION = 20       # seconds to record
CAPTURE_INTERVAL = 1.5    # capture 1 frame every 1.5 seconds
MAX_EMBEDDINGS = 10       # max embeddings to collect
MIN_EMBEDDINGS = 3        # minimum needed to proceed

# Quality / diversity thresholds
MIN_EMB_NORM = 5.0        # reject blurry/partial face embeddings
DIVERSITY_THRESHOLD = 0.92  # reject if too similar to existing (cosine similarity)

# Duplicate detection
DUP_THRESHOLD = 0.85      # flag as duplicate if any embedding matches this well
DUP_MIN_VOTES = 2         # require N embeddings to agree before flagging duplicate

# Upload retry
UPLOAD_RETRIES = 3
UPLOAD_RETRY_DELAY = 2    # seconds between retries

# ==============================
# HAAR CASCADE
# ==============================
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
)


# ==============================
# LOAD USERS FROM API (with retry + caching)
# ==============================
_users_cache = None
_users_cache_time = 0
USERS_CACHE_TTL = 120  # seconds


def load_users(retries=5, timeout=30, use_cache=True):
    global _users_cache, _users_cache_time

    if use_cache and _users_cache is not None:
        if time.time() - _users_cache_time < USERS_CACHE_TTL:
            return _users_cache

    for attempt in range(1, retries + 1):
        try:
            print(f"📡 Connecting to server (attempt {attempt}/{retries})...")
            res = requests.get(
                f"{BASE_URL}/api/get-users/",
                headers={"X-Internal-Key": API_KEY},
                timeout=timeout
            )
            res.raise_for_status()
            data = res.json()
            _users_cache = data
            _users_cache_time = time.time()
            return data
        except requests.exceptions.Timeout:
            wait = attempt * 10
            print(f"⏳ Server waking up... retrying in {wait}s")
            time.sleep(wait)
        except Exception as e:
            wait = attempt * 5
            print(f"❌ Attempt {attempt} failed: {e} — retrying in {wait}s")
            time.sleep(wait)

    return []


# ==============================
# SELECT USER
# ==============================
def select_user(all_users):
    if not all_users:
        print("❌ Could not load users from server.")
        return None

    print("\n📋 Available Users:\n")
    for i, u in enumerate(all_users):
        emb_count = len(u.get("embeddings", []))
        status = "✅ enrolled" if emb_count >= MIN_EMBEDDINGS else "⚠ needs enrollment"
        print(f"  {i+1}. {u['unique_id']} - {u.get('name', 'N/A')} "
              f"({emb_count} embeddings) [{status}]")

    print()
    while True:
        try:
            choice = int(input("👉 Select user number: ")) - 1
            if 0 <= choice < len(all_users):
                return all_users[choice]["unique_id"]
            print(f"   Please enter a number between 1 and {len(all_users)}")
        except ValueError:
            print("   Please enter a valid number")


# ==============================
# EXTRACT EMBEDDING FROM FRAME
# ==============================
def extract_embedding(frame):
    """
    Returns (embedding, facial_area) if exactly one clear face detected.
    enforce_detection=True rejects blurry/partial faces immediately.
    """
    try:
        results = DeepFace.represent(
            img_path=frame,
            model_name="Facenet",
            enforce_detection=True,
            detector_backend="opencv"
        )
        if results and len(results) == 1:
            emb = np.array(results[0]["embedding"], dtype=np.float32)
            area = results[0].get("facial_area", {})

            if np.linalg.norm(emb) < MIN_EMB_NORM:
                return None, None

            return emb, area
        return None, None
    except Exception:
        return None, None


# ==============================
# DIVERSITY CHECK
# ==============================
def is_diverse_enough(new_emb, existing_embs):
    """
    Returns True if new_emb is sufficiently different from all existing
    embeddings (cosine similarity < DIVERSITY_THRESHOLD).
    """
    if not existing_embs:
        return True

    new_norm = np.linalg.norm(new_emb)
    if new_norm == 0:
        return False

    new_unit = new_emb / new_norm

    for emb in existing_embs:
        norm = np.linalg.norm(emb)
        if norm == 0:
            continue
        if float(np.dot(new_unit, emb / norm)) >= DIVERSITY_THRESHOLD:
            return False

    return True


# ==============================
# DUPLICATE CHECK USING FAISS (multi-vote)
# ==============================
def check_duplicate(query_embeddings, all_users, exclude_uid=None):
    """
    Checks if the face being enrolled already exists under a DIFFERENT user.
    Uses ALL collected embeddings and requires DUP_MIN_VOTES matches.
    Returns (is_duplicate, matched_uid, avg_score)
    """
    all_vecs = []
    uid_map = []

    for u in all_users:
        if u["unique_id"] == exclude_uid:
            continue
        for emb in u.get("embeddings", []):
            vec = np.array(emb, dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm == 0:
                continue
            all_vecs.append(vec / norm)
            uid_map.append(u["unique_id"])

    if not all_vecs:
        return False, None, 0.0

    # ✅ Build FAISS index ONCE outside the per-embedding loop
    matrix = np.array(all_vecs, dtype=np.float32)
    index = faiss.IndexFlatIP(matrix.shape[1])
    index.add(matrix)

    vote_scores = {}  # { uid: [score, ...] }

    for query_emb in query_embeddings:
        vec = np.array(query_emb, dtype=np.float32).reshape(1, -1)
        norm = np.linalg.norm(vec)
        if norm == 0:
            continue

        scores, idxs = index.search(vec / norm, 1)
        score = float(scores[0][0])
        idx = int(idxs[0][0])

        if idx == -1 or score < DUP_THRESHOLD:
            continue

        uid = uid_map[idx]
        vote_scores.setdefault(uid, []).append(score)

    if not vote_scores:
        return False, None, 0.0

    best_uid = max(vote_scores, key=lambda u: (len(vote_scores[u]),
                                               np.mean(vote_scores[u])))
    best_votes = len(vote_scores[best_uid])
    best_avg = float(np.mean(vote_scores[best_uid]))

    if best_votes < DUP_MIN_VOTES:
        return False, None, best_avg

    return True, best_uid, best_avg


# ==============================
# CROP FACE FROM FRAME
# ==============================
def crop_face(frame, facial_area=None):
    """
    Crop and resize face region with padding.

    BUG FIX: Prefer facial_area from DeepFace (already validated) over
    re-running Haar cascade, which often fails on the same frame.
    Falls back to Haar only if no facial_area is provided.
    """
    # ✅ Primary: use DeepFace facial_area (x, y, w, h)
    if facial_area and all(k in facial_area for k in ("x", "y", "w", "h")):
        x = facial_area["x"]
        y = facial_area["y"]
        w = facial_area["w"]
        h = facial_area["h"]
    else:
        # Fallback: run Haar cascade
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(
            gray, scaleFactor=1.3, minNeighbors=5, minSize=(80, 80)
        )
        if len(faces) != 1:
            return None
        x, y, w, h = faces[0]

    pad = 30
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(frame.shape[1], x + w + pad)
    y2 = min(frame.shape[0], y + h + pad)
    cropped = frame[y1:y2, x1:x2]

    if cropped.size == 0:
        return None

    return cv2.resize(cropped, (300, 300))


# ==============================
# SEND EMBEDDINGS — BATCH (single API call)
# ==============================
def send_embeddings_batch(unique_id, embeddings):
    """
    Sends ALL embeddings in ONE API call.
    Falls back to one-by-one with retry if batch endpoint unavailable.
    """
    print(f"\n📤 Uploading {len(embeddings)} embeddings (batch)...")

    try:
        payload = {
            "unique_id":  unique_id,
            "embeddings": [emb.tolist() for emb in embeddings],
        }
        res = requests.post(
            f"{BASE_URL}/api/save-embeddings-batch/",
            json=payload,
            headers={"X-Internal-Key": API_KEY},
            timeout=20
        )
        res.raise_for_status()
        print(f"   ✅ All {len(embeddings)} embeddings saved (batch)")
        return len(embeddings)

    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            print("   ℹ️  Batch endpoint not found — falling back to one-by-one upload")
        else:
            print(f"   ⚠️  Batch upload failed ({e}) — falling back to one-by-one upload")
    except Exception as e:
        print(f"   ⚠️  Batch upload error ({e}) — falling back to one-by-one upload")

    # Fallback: one-by-one with retry
    success_count = 0
    for i, emb in enumerate(embeddings):
        saved = False
        for attempt in range(1, UPLOAD_RETRIES + 1):
            try:
                res = requests.post(
                    f"{BASE_URL}/api/save-embedding/",
                    json={
                        "unique_id": unique_id,
                        "embedding": json.dumps(emb.tolist()),
                        "api_key":   API_KEY
                    },
                    timeout=10
                )
                res.raise_for_status()
                success_count += 1
                saved = True
                print(f"   ✅ Embedding {i+1}/{len(embeddings)} saved")
                break
            except Exception as e:
                if attempt < UPLOAD_RETRIES:
                    print(f"   ⚠️  Attempt {attempt} failed, retrying in {UPLOAD_RETRY_DELAY}s...")
                    time.sleep(UPLOAD_RETRY_DELAY)
                else:
                    print(f"   ❌ Embedding {i+1} failed after {UPLOAD_RETRIES} attempts: {e}")

        if not saved:
            print(f"   ❌ Skipping embedding {i+1}")

    return success_count


# ==============================
# SEND FACE IMAGE TO API
# ==============================
def send_face_image(unique_id, face_img):
    """Upload cropped face image (Cloudinary handles storage)."""
    try:
        _, buffer = cv2.imencode('.jpg', face_img,
                                 [cv2.IMWRITE_JPEG_QUALITY, 92])
        img_bytes = buffer.tobytes()

        res = requests.post(
            f"{BASE_URL}/api/upload-face-image/",
            files={"face_image": (f"{unique_id}.jpg", img_bytes, "image/jpeg")},
            data={"unique_id": unique_id},
            headers={"X-Internal-Key": API_KEY},
            timeout=20
        )
        res.raise_for_status()
        data = res.json()

        if data.get("status") == "success":
            print(f"   ✅ Profile image saved → {data.get('image_url')}")
            return True
        else:
            print(f"   ❌ Server error: {data}")
            return False

    except Exception as e:
        print(f"   ❌ Face image upload failed: {e}")
        return False


# ==============================
# DRAW PROGRESS BAR
# ==============================
def draw_progress(frame, collected, total_time, elapsed):
    h, w = frame.shape[:2]
    bar_x, bar_y = 20, h - 50
    bar_w, bar_h = w - 40, 20

    cv2.rectangle(frame, (bar_x, bar_y),
                  (bar_x + bar_w, bar_y + bar_h), (50, 50, 50), -1)

    progress = min(elapsed / total_time, 1.0)
    fill_w = int(bar_w * progress)
    color = (0, 255, 0) if progress < 0.8 else (0, 200, 255)
    cv2.rectangle(frame, (bar_x, bar_y),
                  (bar_x + fill_w, bar_y + bar_h), color, -1)

    cv2.rectangle(frame, (bar_x, bar_y),
                  (bar_x + bar_w, bar_y + bar_h), (200, 200, 200), 1)

    remaining = max(0, total_time - elapsed)
    cv2.putText(frame, f"Recording: {remaining:.1f}s remaining",
                (bar_x, bar_y - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
    cv2.putText(frame, f"Embeddings captured: {collected}/{MAX_EMBEDDINGS}",
                (bar_x, bar_y - 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)


# ==============================
# DRAW FACE BOX
# ==============================
# ✅ Cache last detected face rect to avoid running Haar every single frame.
_last_face_rect = None
_last_face_tick = 0
FACE_DETECT_INTERVAL = 0.15   # re-detect at most every 150ms (~7fps)


def draw_face_box(frame, status):
    """
    status: 'good' | 'analyzing' | 'similar' | 'none' | 'multiple'
    Uses a short-lived cache to avoid running Haar cascade on every display frame.
    """
    global _last_face_rect, _last_face_tick

    now = time.time()
    if now - _last_face_tick >= FACE_DETECT_INTERVAL:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(
            gray, scaleFactor=1.3, minNeighbors=5, minSize=(80, 80)
        )
        _last_face_tick = now
        _last_face_rect = faces if len(faces) > 0 else []

    faces = _last_face_rect

    color_map = {
        "good":      (0, 255, 0),
        "analyzing": (0, 165, 255),
        "similar":   (0, 200, 255),
    }
    label_map = {
        "good":      "Captured",
        "analyzing": "Analyzing...",
        "similar":   "Too similar — move slightly",
    }

    if len(faces) == 1:
        x, y, w, h = faces[0]
        color = color_map.get(status, (0, 165, 255))
        cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
        label = label_map.get(status, "Analyzing...")
        cv2.putText(frame, label, (x, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
    elif len(faces) == 0:
        cv2.putText(frame, "No Face — Move Closer", (20, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    else:
        cv2.putText(frame, "Multiple Faces Detected", (20, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)


# ==============================
# MAIN ENROLL FLOW
# ==============================
def enroll():
    all_users = load_users(use_cache=False)
    unique_id = select_user(all_users)
    if not unique_id:
        print("❌ No user selected. Exiting.")
        return

    print(f"\n🎯 Enrolling: {unique_id}")
    print(f"📹 Recording for {VIDEO_DURATION} seconds...")
    print("   → Look straight at the camera")
    print("   → Slowly turn your head left and right")
    print("   → Try different expressions\n")
    input("   Press ENTER when ready to start recording...")

    print("\n🔥 Warming up face model...")
    dummy = np.zeros((160, 160, 3), dtype=np.uint8)
    DeepFace.represent(dummy, model_name="Facenet",
                       enforce_detection=False, detector_backend="opencv")
    print("✅ Model ready.\n")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ Could not open camera.")
        return

    cv2.namedWindow("Enrolling Face", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Enrolling Face", 700, 500)
    cv2.setWindowProperty("Enrolling Face", cv2.WND_PROP_TOPMOST, 1)

    embeddings = []
    best_frame = None
    best_area = None      # ✅ store facial_area for reliable cropping
    last_capture = 0
    start_time = time.time()
    face_status = "analyzing"

    print("🎬 Recording started!\n")

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        elapsed = time.time() - start_time
        display = frame.copy()
        still_going = elapsed < VIDEO_DURATION
        can_capture = (len(embeddings) < MAX_EMBEDDINGS and still_going)

        if can_capture and (time.time() - last_capture) >= CAPTURE_INTERVAL:
            last_capture = time.time()
            emb, area = extract_embedding(frame)

            if emb is not None:
                if is_diverse_enough(emb, embeddings):
                    embeddings.append(emb)
                    face_status = "good"

                    # ✅ Save frame AND its facial_area together
                    if best_frame is None:
                        best_frame = frame.copy()
                        best_area = area

                    print(f"   📸 Captured embedding {len(embeddings)}/{MAX_EMBEDDINGS}")
                else:
                    face_status = "similar"
                    print("   ⏭  Skipped (too similar to existing embeddings)")
            else:
                face_status = "analyzing"

        draw_face_box(display, face_status)
        draw_progress(display, len(embeddings), VIDEO_DURATION, elapsed)

        cv2.putText(display, f"Enrolling: {unique_id}", (20, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)

        if not still_going or len(embeddings) >= MAX_EMBEDDINGS:
            cv2.putText(display, "Recording Complete!", (20, 80),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            cv2.imshow("Enrolling Face", display)
            cv2.waitKey(1500)
            break

        cv2.imshow("Enrolling Face", display)
        if cv2.waitKey(1) == 27:
            print("\n⚠️  Enrollment cancelled.")
            cap.release()
            cv2.destroyAllWindows()
            return

    cap.release()
    cv2.destroyAllWindows()

    print(f"\n{'='*40}")
    print(f"📊 Recording complete!")
    print(f"   Embeddings collected : {len(embeddings)}")
    print(f"   Duration             : {VIDEO_DURATION}s")
    print(f"{'='*40}\n")

    if len(embeddings) < MIN_EMBEDDINGS:
        print(f"❌ Only {len(embeddings)} embeddings collected "
              f"(minimum: {MIN_EMBEDDINGS}).")
        print("   Please try again with better lighting and face position.")
        return

    print("🔍 Checking for duplicate face in existing users...")
    is_dup, dup_id, dup_score = check_duplicate(
        embeddings, all_users, exclude_uid=unique_id
    )

    if is_dup:
        print(f"\n⚠️  Duplicate face detected!")
        print(f"   Matches existing user : {dup_id}")
        print(f"   Similarity score      : {round(dup_score * 100, 1)}%")
        confirm = input("   Continue enrollment anyway? (y/n): ").strip().lower()
        if confirm != 'y':
            print("❌ Enrollment cancelled.")
            return
    else:
        print("   ✅ No duplicate found — proceeding.")

    saved = send_embeddings_batch(unique_id, embeddings)

    print(f"\n{'='*40}")
    print("📸 Processing profile image...")
    if best_frame is not None:
        # ✅ Pass facial_area so crop_face doesn't need to re-run Haar cascade
        face_img = crop_face(best_frame, facial_area=best_area)
        if face_img is not None:
            send_face_image(unique_id, face_img)
        else:
            print("   ⚠️  Could not crop face — skipping profile image.")
    else:
        print("   ⚠️  No clean frame captured — skipping profile image.")

    print(f"\n{'='*40}")
    if saved >= MIN_EMBEDDINGS:
        print(f"✅ Enrollment complete!")
        print(f"   User      : {unique_id}")
        print(f"   Saved     : {saved}/{len(embeddings)} embeddings")
        print(f"   Diverse   : Yes (similarity < {DIVERSITY_THRESHOLD})")
        print(f"   Status    : Ready for attendance ✅")
    else:
        print(f"⚠️  Only {saved} embeddings saved successfully.")
        print("   Please check your server connection and try again.")
    print(f"{'='*40}\n")


# ==============================
# RUN
# ==============================
if __name__ == "__main__":
    enroll()