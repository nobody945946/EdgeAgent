def parse(text):
    parts = [int(p) for p in text.split(":")]
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds
    minutes, seconds = parts
    return minutes * 60 + seconds
