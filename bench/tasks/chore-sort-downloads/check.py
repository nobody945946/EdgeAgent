import hashlib, os, sys
from pathlib import Path
orig, final = Path(os.environ["BENCH_ORIG"]) / "下載", Path("下載")
expect = {"圖片": {".jpg", ".png", ".gif", ".webp"}, "文件": {".pdf", ".docx", ".xlsx", ".pptx", ".txt"},
          "壓縮檔": {".zip", ".rar", ".7z"}, "影音": {".mp4", ".m4a", ".mp3", ".mov"}}
def h(p): return hashlib.sha256(p.read_bytes()).hexdigest()
errors = []
before = {h(p): p.name for p in orig.iterdir()}
after = {h(p): p for p in final.rglob("*") if p.is_file()}
for digest, name in before.items():
    if digest not in after:
        errors.append(f"missing {name}"); continue
    p = after[digest]
    folder = p.relative_to(final).parts[0] if len(p.relative_to(final).parts) > 1 else ""
    want = next((k for k, v in expect.items() if Path(name).suffix.lower() in v), "其他")
    if folder != want:
        errors.append(f"{name} in {folder or 'root'} want {want}")
print("\n".join(errors) or "ok"); sys.exit(1 if errors else 0)
