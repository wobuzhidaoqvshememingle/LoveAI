import cv2, json, os, sys
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
mp4 = os.path.join(out, "demo.mp4")
cap = cv2.VideoCapture(mp4)
n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps = cap.get(cv2.CAP_PROP_FPS)
ok_count = 0
while True:
    ret, frame = cap.read()
    if not ret:
        break
    ok_count += 1
cap.release()
result = {"mp4": mp4, "declared_frames": n, "decoded_frames": ok_count,
          "width": w, "height": h, "fps": round(fps, 2),
          "decodable": ok_count == n and n > 0}
print(json.dumps(result, indent=2))
with open(os.path.join(out, "mp4_verify.json"), "w") as f:
    json.dump(result, f, indent=2)
sys.exit(0 if result["decodable"] else 1)
