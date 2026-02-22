import urllib.request
try:
    r = urllib.request.urlopen("http://ollama:11434/api/tags", timeout=5)
    print("OK", r.status)
except Exception as e:
    print("FAIL", type(e).__name__, str(e)[:100])
