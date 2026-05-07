reclaim_str = "10.5GB (99%)"
size_str = reclaim_str.split(" ", maxsplit=1)[0].upper()

multiplier = 1
if "GB" in size_str:
    multiplier = 1073741824
    size_str = size_str.replace("GB", "")
elif "MB" in size_str:
    multiplier = 1048576
    size_str = size_str.replace("MB", "")

print("size_str:", size_str)
print("isdigit:", size_str.replace(".", "", 1).isdigit())
if size_str.replace(".", "", 1).isdigit():
    reclaimable_bytes = int(float(size_str) * multiplier)
    print("reclaimable_bytes:", reclaimable_bytes)
