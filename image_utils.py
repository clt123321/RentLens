import re


def upgrade_image_url(url, source=""):
    if not url or not url.startswith("http"):
        return url if url else "No Image"

    upgraded = url

    if "ljcdn.com" in url or "ke.com" in url:
        upgraded = re.sub(r'/\d+x\d+/', '/600x450/', url)
        upgraded = re.sub(r'_\d+x\d+\.', '_600x450.', upgraded)
        if upgraded == url:
            upgraded = re.sub(r'\.jpg$', '.jpg_600x450.jpg', url, flags=re.IGNORECASE)

    elif "ziroom.com" in url or "ziroom" in url:
        upgraded = re.sub(r'/\d+x\d+/', '/600x450/', url)
        upgraded = re.sub(r'_\d+x\d+\.', '_600x450.', upgraded)
        upgraded = re.sub(r'thumb_\d+', 'thumb_600', upgraded)

    elif "anjuke" in url or "anjuke.com" in url or "ajkimg" in url:
        upgraded = re.sub(r'/\d+x\d+/', '/600x450/', url)
        upgraded = re.sub(r'_\d+x\d+\.', '_600x450.', upgraded)
        upgraded = re.sub(r't_\d+x\d+', 't_600x450', upgraded)
        if "ajkimg" in url:
            upgraded = re.sub(r'/\d+/\d+/', '/600/450/', url)

    elif "58.com" in url or "58cdn" in url or "zhuanlan" in url:
        upgraded = re.sub(r'/\d+x\d+/', '/600x450/', url)
        upgraded = re.sub(r'_\d+x\d+\.', '_600x450.', upgraded)
        upgraded = re.sub(r'thumb_\d+', 'thumb_600', upgraded)

    return upgraded if upgraded else url
