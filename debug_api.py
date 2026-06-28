import requests
import json

BASE_URL = "https://golden-gym.entergym.in"
API_KEY  = "Bismillahir-Rahmanir-Rahim@786"
GYM_ID   = "e928030c-7725-484b-9925-7fc6851b8929"  # replace before running

print("=" * 50)
print("TEST 1: get-users WITHOUT gym_id")
print("=" * 50)
try:
    res = requests.get(
        f"{BASE_URL}/api/get-users/",
        headers={"X-Internal-Key": API_KEY},
        timeout=15
    )
    print(f"Status: {res.status_code}")
    print(f"Body:   {res.text[:300]}")
except Exception as e:
    print(f"Error: {e}")

print()
print("=" * 50)
print("TEST 2: get-users WITH gym_id")
print("=" * 50)
try:
    res = requests.get(
        f"{BASE_URL}/api/get-users/",
        headers={"X-Internal-Key": API_KEY},
        params={"gym_id": GYM_ID},
        timeout=15
    )
    print(f"Status: {res.status_code}")
    print(f"Body:   {res.text[:300]}")
except Exception as e:
    print(f"Error: {e}")

print()
print("=" * 50)
print("TEST 3: get-users WITHOUT auth key (should 403)")
print("=" * 50)
try:
    res = requests.get(
        f"{BASE_URL}/api/get-users/",
        timeout=15
    )
    print(f"Status: {res.status_code}")
    print(f"Body:   {res.text[:300]}")
except Exception as e:
    print(f"Error: {e}")

print()
print("=" * 50)
print("TEST 4: check INTERNAL_API_KEY env matches")
print("=" * 50)
print("Key sent in header: X-Internal-Key:", API_KEY)
print("Make sure INTERNAL_API_KEY env var on Render matches exactly.")