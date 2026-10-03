def parse_kv(line):
    """'a=1;b=2' -> {'a': '1', 'b': '2'}"""
    result = {}
    for part in line.split(";"):
        if part.strip():
            key, value = part.split("=", 1)
            result[key.strip()] = value.strip()
    return result
