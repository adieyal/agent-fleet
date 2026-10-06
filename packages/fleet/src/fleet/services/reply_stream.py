"""Decode only the reply string from an incomplete structured model output."""
import json
import threading
import time


def partial_reply(encoded: str) -> str:
    decoder = json.JSONDecoder()
    source = encoded.lstrip()
    if not source.startswith('{'):
        return ''
    source = source[1:].lstrip()
    while source:
        try:
            key, end = decoder.raw_decode(source)
        except ValueError:
            return ''
        source = source[end:].lstrip()
        if not source.startswith(':'):
            return ''
        source = source[1:].lstrip()
        if key == 'reply':
            if not source.startswith('"'):
                return ''
            # Decode complete characters only, including split JSON escapes.
            text, index = [], 1
            while index < len(source):
                char = source[index]
                if char == '"':
                    break
                if char == '\\':
                    width = 6 if source[index:index + 2] == '\\u' else 2
                    if index + width > len(source):
                        break
                    try:
                        char = json.loads('"' + source[index:index + width] + '"')
                    except ValueError:
                        break
                    # Surrogate pairs must be decoded together, never sent as invalid UTF-8.
                    if 0xD800 <= ord(char) <= 0xDBFF:
                        if index + 12 > len(source):
                            break
                        try:
                            char = json.loads('"' + source[index:index + 12] + '"')
                        except ValueError:
                            break
                        width = 12
                    if any(0xD800 <= ord(part) <= 0xDFFF for part in char):
                        break
                    index += width
                else:
                    if ord(char) < 32:
                        break
                    index += 1
                text.append(char)
                if len(text) >= 8192:
                    break
            return ''.join(text)
        try:
            _, end = decoder.raw_decode(source)
        except ValueError:
            return ''
        source = source[end:].lstrip()
        if not source.startswith(','):
            return ''
        source = source[1:].lstrip()
    return ''


class ReplyStream:
    """Leading and trailing updates: a paused model still exposes its latest text."""
    def __init__(self, publish, interval: float = 0.1):
        self.publish, self.interval = publish, interval
        self.lock = threading.RLock()
        self.encoded, self.last_text = '', ''
        self.sent = float('-inf')
        self.timer = None
        self.closed = False

    def delta(self, chunk: str) -> None:
        with self.lock:
            if self.closed:
                return
            self.encoded = (self.encoded + chunk)[:65_536]
            delay = self.interval - (time.monotonic() - self.sent)
            if delay <= 0:
                self._emit()
            elif self.timer is None:
                self.timer = threading.Timer(delay, self._emit)
                self.timer.daemon = True
                self.timer.start()

    def _emit(self) -> None:
        with self.lock:
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
            if self.closed:
                return
            text = partial_reply(self.encoded)
            if text and text != self.last_text:
                now = time.monotonic()
                delay = self.interval - (now - self.sent)
                if delay > 0:
                    self.timer = threading.Timer(delay, self._emit)
                    self.timer.daemon = True
                    self.timer.start()
                    return
                self.sent, self.last_text = now, text
                self.publish(text)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
