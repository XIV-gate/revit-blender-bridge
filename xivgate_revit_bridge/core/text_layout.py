"""Word wrapping with an injected font measurement, independent of the host."""
import re


def pixel_wrap(text, width, measure):
    """Fit words to measured width; break oversized paths at separators first.

    Explicit paragraphs survive. Whitespace inside a paragraph is normalized,
    while every non-whitespace character survives even at a one-pixel width.
    """
    if not text or not str(text).strip():
        return []
    width = max(1, width)
    lines = []
    for paragraph in str(text).splitlines():
        current = ''
        words = paragraph.split()
        # Short Russian prepositions belong with the following word whenever
        # the pair fits, rather than dangling at the preceding line's end.
        for index in range(len(words) - 2, -1, -1):
            if words[index].lower() in {'в', 'во', 'с', 'со', 'к', 'ко', 'у', 'о', 'об', 'от', 'до', 'из', 'на', 'по', 'за', 'и', 'а', 'не'}:
                pair = words[index] + ' ' + words[index + 1]
                if measure(pair) <= width:
                    words[index:index + 2] = [pair]
        for word in words:
            candidate = current + (' ' if current else '') + word
            if measure(candidate) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ''
            while measure(word) > width:
                # Binary search avoids quadratic measurements on long paths.
                low, high = 1, len(word)
                while low < high:
                    middle = (low + high + 1) // 2
                    if measure(word[:middle]) <= width:
                        low = middle
                    else:
                        high = middle - 1
                count = low
                separators = list(re.finditer(r'[/\\\-_.]', word[:count]))
                if separators:
                    count = separators[-1].end()
                lines.append(word[:count])
                word = word[count:]
            current = word
        if current or not paragraph.strip():
            lines.append(current)
    return lines
