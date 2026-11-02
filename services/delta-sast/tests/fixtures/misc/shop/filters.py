"""직접 만든 필터."""


def clean(text: str) -> str:
    # script 태그만 지운다.
    return text.replace("<script>", "").replace("</script>", "")
