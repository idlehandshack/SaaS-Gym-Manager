import os
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

import absl.logging
absl.logging.set_verbosity(absl.logging.ERROR)
absl.logging.use_absl_handler()

import logging
logging.getLogger('tensorflow').setLevel(logging.ERROR)
logging.getLogger('absl').setLevel(logging.ERROR)

from deepface import DeepFace
import faiss
import time
import threading
import requests
import numpy as np
import cv2

# ==============================
# SOUND SETUP
# ==============================
try:
    import pygame
    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
    SOUND_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pc_access.mp3")
    if os.path.exists(SOUND_PATH):
        attendance_sound = pygame.mixer.Sound(SOUND_PATH)
        print(f"✅ Sound loaded: {SOUND_PATH}")
    else:
        attendance_sound = None
        print(f"⚠️  Sound file not found at: {SOUND_PATH}")
    SOUND_AVAILABLE = True
except Exception as e:
    SOUND_AVAILABLE = False
    attendance_sound = None
    print(f"⚠️  pygame not available, sound disabled: {e}")


def play_attendance_sound():
    """Play the attendance sound in a non-blocking way."""
    if SOUND_AVAILABLE and attendance_sound:
        try:
            attendance_sound.play()
        except Exception as e:
            print(f"⚠️  Sound playback error: {e}")


# ==============================
# CONFIG
# ==============================
# API_URL              = "http://127.0.0.1:8000/api/mark-attendance/"
# USERS_API            = "http://127.0.0.1:8000/api/get-users/"
API_URL              = "https://entergym.onrender.com/api/mark-attendance/"
USERS_API            = "https://entergym.onrender.com/api/get-users/"
API_KEY              = "mysecret123"

THRESHOLD            = 0.82   # Cosine similarity cutoff
MIN_VOTES            = 2      # Minimum votes to confirm a match
CONFIDENCE_GAP       = 0.04   # Winner must beat 2nd place by this margin
MIN_FACE_SIZE        = 80     # Minimum face region size in pixels
MIN_EMB_NORM         = 5.0    # Minimum embedding norm (rejects blurry faces)

COOLDOWN             = 60     # Seconds before re-marking same user
USER_RELOAD_INTERVAL = 60     # Seconds between user list refresh
MSG_DURATION         = 3      # Seconds to show attendance message on screen
INFERENCE_INTERVAL   = 0.3    # Seconds between DeepFace runs
MSG_CLEANUP_INTERVAL = 5.0    # Throttle message dict cleanup (not every frame)
FAISS_K              = 50     # Cap K — searching all embeddings is wasteful
API_RETRIES          = 2      # Retry count on API failure
CAMERA_FAIL_LIMIT    = 30     # Consecutive read failures before reconnect

# ==============================
# STATE
# ==============================

last_sent            = {}     # { unique_id: timestamp }
last_sent_lock       = threading.Lock()

last_reload          = 0
last_inference       = 0

# FIX: last_msg_cleanup now protected by attendance_lock (was read outside
#      the lock while written inside it, causing a race condition)
last_msg_cleanup     = 0

# Reload guard — prevents double-trigger if reload thread is slow
_reload_in_progress  = False
_reload_lock         = threading.Lock()

users                = []     # [{ unique_id, name, embeddings: [np.array] }]
users_lock           = threading.Lock()

# O(1) uid -> user dict lookup instead of linear scan
users_map            = {}     # { unique_id: user_dict }
users_map_lock       = threading.Lock()

faiss_index          = None
faiss_uid_map        = []
faiss_lock           = threading.Lock()

attendance_messages  = {}     # { unique_id: (msg, timestamp) }
attendance_lock      = threading.Lock()

_last_user_count     = 0

_last_user_count_lock = threading.Lock()

_inference_in_progress = False
_inference_lock      = threading.Lock()

consecutive_failures = 0      # Only touched in the main thread — no lock needed

# ==============================
# HAAR CASCADE (init once)
# ==============================
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
)


# ==============================
# LOAD USERS FROM API
# ==============================
def load_users():
    try:
        res = requests.get(USERS_API, headers={"X-Internal-Key": API_KEY}, timeout=10)
        res.raise_for_status()
        data = res.json()

        formatted = []
        for u in data:
            embeddings = u.get("embeddings", [])
            emb_list = [np.array(e, dtype=np.float32) for e in embeddings]
            if not emb_list:
                continue
            formatted.append({
                "unique_id": u["unique_id"],
                "name":      u.get("name", u["unique_id"]),
                "embeddings": emb_list
            })

        print(f"✅ Loaded {len(formatted)} users")
        return formatted

    except Exception as e:
        print("❌ Failed to load users:", e)
        return []


# ==============================
# BUILD FAISS INDEX
# ==============================
def build_faiss_index(user_list):
    """
    Builds a flat inner-product FAISS index.
    Vectors are L2-normalised so inner product == cosine similarity.

    FIX: _last_user_count is now read and written under _last_user_count_lock
         to prevent a race where two concurrent reload threads could both
         see the stale count, both decide to rebuild, and corrupt the index.
    FIX: also builds the O(1) users_map lookup dict.
    """
    global faiss_index, faiss_uid_map, users_map

    with _last_user_count_lock:
        if len(user_list) == _last_user_count and faiss_index is not None:
            print("⚡ FAISS index unchanged — skipping rebuild.")
            return

    all_embeddings = []
    uid_map        = []
    new_users_map  = {}

    for u in user_list:
        new_users_map[u["unique_id"]] = u
        added = 0
        for emb in u["embeddings"]:
            norm = np.linalg.norm(emb)
            if norm == 0:
                continue
            all_embeddings.append(emb / norm)
            uid_map.append(u["unique_id"])
            added += 1
        if added == 0:
            print(f"⚠️  User {u['unique_id']} has no valid embeddings, skipping.")

    if not all_embeddings:
        print("⚠️  No embeddings available — FAISS index not built.")
        return

    # Build matrix outside the lock, then swap atomically
    matrix = np.array(all_embeddings, dtype=np.float32)
    dim    = matrix.shape[1]
    index  = faiss.IndexFlatIP(dim)
    index.add(matrix)

    with faiss_lock:
        faiss_index   = index
        faiss_uid_map = uid_map

    with users_map_lock:
        users_map = new_users_map

    with _last_user_count_lock:
        # FIX: use a module-level variable correctly via globals()
        import sys
        this = sys.modules[__name__]
        this._last_user_count = len(user_list)

    avg_emb = len(all_embeddings) / len(user_list) if user_list else 0
    print(f"✅ FAISS index built: {index.ntotal} embeddings | "
          f"{len(user_list)} users | avg {avg_emb:.1f} emb/user | dim={dim}")


# ==============================
# FIND BEST MATCH VIA FAISS (Top-K Voting)
# ==============================
def find_best_match(face_emb):
    """
    Searches top-K matches in FAISS using majority voting.
    K capped at FAISS_K (50) instead of searching all embeddings.
    User dict lookup is O(1) via users_map.
    Returns (user_dict, avg_score) or (None, 0.0).
    """
    with faiss_lock:
        index   = faiss_index
        uid_map = list(faiss_uid_map)

    if index is None or index.ntotal == 0:
        return None, 0.0

    norm = np.linalg.norm(face_emb)
    if norm == 0:
        return None, 0.0

    K     = min(index.ntotal, FAISS_K)
    query = np.array([face_emb / norm], dtype=np.float32)
    scores, idxs = index.search(query, k=K)

    vote_scores = {}
    for rank in range(K):
        idx   = int(idxs[0][rank])
        score = float(scores[0][rank])
        if idx == -1 or score < THRESHOLD:
            continue
        uid = uid_map[idx]
        vote_scores.setdefault(uid, []).append(score)

    if not vote_scores:
        return None, 0.0

    ranked = sorted(
        vote_scores.items(),
        key=lambda item: (len(item[1]), np.mean(item[1])),
        reverse=True
    )

    best_uid, best_scores = ranked[0]
    best_votes = len(best_scores)
    best_avg   = float(np.mean(best_scores))

    if best_votes < MIN_VOTES:
        print(f"   ❌ Rejected: only {best_votes} vote(s) for {best_uid} "
              f"(need {MIN_VOTES})")
        return None, 0.0

    if len(ranked) > 1:
        second_avg = float(np.mean(ranked[1][1]))
        gap        = best_avg - second_avg
        if gap < CONFIDENCE_GAP:
            print(f"   ❌ Ambiguous: {best_uid}({round(best_avg,3)}) vs "
                  f"{ranked[1][0]}({round(second_avg,3)}) | gap={round(gap,3)}")
            return None, 0.0

    print(f"   ✅ Confirmed: {best_uid} | votes={best_votes} | "
          f"avg_score={round(best_avg, 3)}")

    with users_map_lock:
        user = users_map.get(best_uid)

    return (user, best_avg) if user else (None, 0.0)


def reload_users_async():
    global users, last_reload, _reload_in_progress
    try:
        new_users = load_users()
        if not new_users:
            print("⚠️  Reload returned empty list — keeping existing users.")
            return
        with users_lock:
            users = new_users
        build_faiss_index(new_users)
        last_reload = time.time()
        print("🔄 Users reloaded and FAISS index rebuilt.")
    finally:
        with _reload_lock:
            _reload_in_progress = False


def send_attendance(unique_id):
    """POST attendance with retry on transient failures."""
    for attempt in range(1, API_RETRIES + 1):
        try:
            res = requests.post(
                API_URL,
                json={"unique_id": unique_id},
                headers={"X-Internal-Key": API_KEY},
                timeout=5
            )
            res.raise_for_status()
            return res.json()
        except Exception as e:
            print(f"❌ API Error for {unique_id} (attempt {attempt}/{API_RETRIES}): {e}")
            if attempt < API_RETRIES:
                time.sleep(1)
    return None


def send_attendance_async(uid, name, timestamp):
    result = send_attendance(uid)

    if result is None:
        with last_sent_lock:
            last_sent.pop(uid, None)
        print(f"⚠️  Attendance failed for {uid} after {API_RETRIES} attempts — cooldown released.")
        return

    status = result.get("status", "")
    if status == "success":
        msg = f"✔ Attendance Marked: {uid} - {name}"
        threading.Thread(target=play_attendance_sound, daemon=True).start()
    elif status == "exists":
        msg = f"⚠ Already Marked: {uid} - {name}"
        threading.Thread(target=play_attendance_sound, daemon=True).start()
    else:
        msg = f"❌ Error for {uid}: {result.get('message', 'Unknown error')}"
        # FIX: release cooldown under lock
        with last_sent_lock:
            last_sent.pop(uid, None)

    print(msg)
    with attendance_lock:
        attendance_messages[uid] = (msg, timestamp)


# ==============================
# INFERENCE (background thread)
# Logs when DeepFace returns empty results
# ==============================
def run_inference_async(frame, current_time):
    global _inference_in_progress
    try:
        face_results = DeepFace.represent(
            img_path=frame,
            model_name="Facenet",
            enforce_detection=False,
            detector_backend="opencv"
        )

        if not face_results:
            print("   ℹ️  DeepFace: no face embeddings returned.")
            return

        processed_in_frame = set()

        for face_data in face_results:
            region = face_data.get("facial_area", {})
            fw     = region.get("w", 50)
            fh     = region.get("h", 50)

            if fw < MIN_FACE_SIZE or fh < MIN_FACE_SIZE:
                print(f"   ⏭ Skipping small face region: {fw}x{fh}")
                continue

            face_emb = np.array(face_data["embedding"], dtype=np.float32)
            emb_norm = np.linalg.norm(face_emb)

            if emb_norm < MIN_EMB_NORM:
                print(f"   ⏭ Low-confidence embedding (norm={round(emb_norm, 2)})")
                continue

            matched_user, score = find_best_match(face_emb)
            if matched_user is None:
                continue

            uid  = matched_user["unique_id"]
            name = matched_user.get("name", uid)

            if uid in processed_in_frame:
                continue
            processed_in_frame.add(uid)

            # FIX: cooldown check now under last_sent_lock to prevent TOCTOU
            #      race where two threads could both pass the check and both
            #      submit attendance for the same user in the same cycle
            with last_sent_lock:
                in_cooldown = (
                    uid in last_sent and
                    current_time - last_sent[uid] < COOLDOWN
                )
                if not in_cooldown:
                    last_sent[uid] = current_time  # optimistic; cleared on failure

            if in_cooldown:
                continue

            print(f"🔍 Match → {uid} | {name} | score={round(score, 3)}")
            threading.Thread(
                target=send_attendance_async,
                args=(uid, name, current_time),
                daemon=True
            ).start()

    except Exception as e:
        print("⚠️  DeepFace Error:", e)
    finally:
        with _inference_lock:
            _inference_in_progress = False


# ==============================
# CAMERA RECONNECT HELPER
# ==============================
def try_reconnect_camera(cap):
    print("🔄 Attempting camera reconnect...")
    cap.release()
    time.sleep(1)
    cap.open(0)
    if cap.isOpened():
        print("✅ Camera reconnected.")
        return True
    print("❌ Camera reconnect failed.")
    return False

print("🚀 Starting Face Attendance System...")
users = load_users()
last_reload = time.time()
build_faiss_index(users)

print("🔥 Warming up DeepFace model...")
try:
    dummy = np.zeros((160, 160, 3), dtype=np.uint8)
    DeepFace.represent(
        img_path=dummy,
        model_name="Facenet",
        enforce_detection=False,
        detector_backend="opencv"
    )
    print("✅ Model ready.")
except Exception as e:
    print(f"❌ DeepFace warmup failed: {e}")
    print("   Ensure model weights are downloaded or network is available.")
    exit(1)

# ==============================
# CAMERA SETUP
# ==============================
video = cv2.VideoCapture(0)
if not video.isOpened():
    print("❌ Could not open camera. Exiting.")
    exit(1)

cv2.namedWindow("Face Attendance", cv2.WINDOW_NORMAL)
cv2.resizeWindow("Face Attendance", 900, 600)
print("🎥 Camera started. Press ESC to exit.\n")

# ==============================
# MAIN LOOP
# ==============================
while True:
    ret, frame = video.read()

    # Camera reconnect after CAMERA_FAIL_LIMIT consecutive failures
    if not ret:
        consecutive_failures += 1
        print(f"⚠️  Frame grab failed ({consecutive_failures}/{CAMERA_FAIL_LIMIT})...")
        if consecutive_failures >= CAMERA_FAIL_LIMIT:
            if not try_reconnect_camera(video):
                print("❌ Could not reconnect camera. Exiting.")
                break
            consecutive_failures = 0
        time.sleep(0.05)
        continue
    consecutive_failures = 0

    current_time  = time.time()
    display_frame = frame.copy()

    # --------------------------------------------------
    # HAAR CASCADE: fast pre-screen for faces
    # --------------------------------------------------
    gray       = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    haar_faces = face_cascade.detectMultiScale(
        gray, scaleFactor=1.3, minNeighbors=5, minSize=(60, 60)
    )

    if len(haar_faces) == 0:
        cv2.putText(display_frame, "No Face Detected", (20, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
    else:
        cv2.putText(display_frame,
                    f"Scanning {len(haar_faces)} face(s)...", (20, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)

        with _inference_lock:
            can_infer = (not _inference_in_progress and
                         current_time - last_inference >= INFERENCE_INTERVAL)
            if can_infer:
                _inference_in_progress = True
                last_inference = current_time

        if can_infer:
            threading.Thread(
                target=run_inference_async,
                args=(frame.copy(), current_time),
                daemon=True
            ).start()

    with attendance_lock:
        active_msgs = [
            (uid, msg, ts)
            for uid, (msg, ts) in attendance_messages.items()
            if current_time - ts < MSG_DURATION
        ]
        if current_time - last_msg_cleanup > MSG_CLEANUP_INTERVAL:
            cleaned = {
                uid: (msg, ts)
                for uid, (msg, ts) in attendance_messages.items()
                if current_time - ts < MSG_DURATION * 2
            }
            attendance_messages.clear()
            attendance_messages.update(cleaned)
            last_msg_cleanup = current_time   # FIX: written inside the lock

    for i, (uid, msg, ts) in enumerate(active_msgs):
        cv2.putText(display_frame, msg, (20, 100 + i * 35),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2)

    # --------------------------------------------------
    # STATS OVERLAY
    # --------------------------------------------------
    with faiss_lock:
        total_indexed = faiss_index.ntotal if faiss_index else 0
    with users_lock:
        total_users = len(users)

    stats = (f"Users: {total_users} | Indexed: {total_indexed} | "
             f"Inference: {round(1/INFERENCE_INTERVAL)}hz | "
             f"Threshold: {THRESHOLD} | MinVotes: {MIN_VOTES} | Gap: {CONFIDENCE_GAP}")
    cv2.putText(display_frame, stats,
                (10, display_frame.shape[0] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)

    cv2.imshow("Face Attendance", display_frame)

    key = cv2.waitKey(1) & 0xFF
    if key == 27:
        print("👋 Exiting...")
        break
    if key == ord('q'):
        print("⚡ Force Exit")
        break

    # --------------------------------------------------
    # AUTO RELOAD — guard prevents double-trigger if thread is slow
    # --------------------------------------------------
    if current_time - last_reload > USER_RELOAD_INTERVAL:
        with _reload_lock:
            if not _reload_in_progress:
                _reload_in_progress = True
                last_reload = current_time
                threading.Thread(target=reload_users_async, daemon=True).start()
                print("🔄 Reloading users in background...")

# ==============================
# CLEANUP
# ==============================
if video.isOpened():
    video.release()

if SOUND_AVAILABLE:
    pygame.mixer.quit()

cv2.destroyAllWindows()
print("✅ Shutdown complete.")