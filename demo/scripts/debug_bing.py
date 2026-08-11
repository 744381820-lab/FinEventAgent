import sys, io, asyncio, httpx, re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


async def main():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    async with httpx.AsyncClient(timeout=20.0, headers=headers, follow_redirects=True) as client:
        resp = await client.get(
            "https://cn.bing.com/search",
            params={"q": "宁德时代 半年报", "count": 5, "setlang": "zh-CN"},
        )
        html = resp.text

    for pat in [
        r"b_algo",
        r"b_title",
        r'class="b_caption"',
        r"<cite>",
        r'ol id="b_results"',
        r'class="b_algoheader"',
    ]:
        print(pat, "->", len(re.findall(pat, html)))

    m = re.search(r'ol id="b_results"(.*?)(?:ol id="b_context"|</main>|$)', html, re.S)
    if not m:
        print("no b_results")
        return
    chunk = m.group(1)
    print("results chunk len", len(chunk))
    # Print first 2500 chars of results area
    print(chunk[:2500])
    print("--- titles ---")
    for h in re.findall(r"<h2[^>]*>.*?</h2>", chunk, re.S)[:5]:
        print(re.sub(r"\s+", " ", h)[:200])


asyncio.run(main())
