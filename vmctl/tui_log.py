"""Stream guest logs as text without letting their terminal controls reach the TUI."""
from __future__ import annotations

import codecs
import os
import sys


class LogText:
    """Discard terminal escape sequences, including ones split across reads."""

    def __init__(self) -> None:
        self.state = "text"
        self.after_cr = False

    def feed(self, text: str) -> str:
        output = []
        for char in text:
            if self.state == "string":
                if char in ("\x07", "\x9c"):
                    self.state = "text"
                elif char == "\x1b":
                    self.state = "string_escape"
            elif self.state == "string_escape":
                self.state = "text" if char == "\\" else "string"
            elif char == "\x1b":
                self.state = "escape"
            elif self.state == "escape":
                if char == "[":
                    self.state = "csi"
                elif char in "]PX^_":
                    self.state = "string"
                elif " " <= char <= "/":
                    self.state = "intermediate"
                else:
                    self.state = "text"
            elif self.state in ("csi", "intermediate"):
                if "@" <= char <= "~" or (self.state == "intermediate" and "0" <= char <= "?"):
                    self.state = "text"
            elif char == "\x9b":
                self.state = "csi"
            elif char in "\x90\x98\x9d\x9e\x9f":
                self.state = "string"
            elif char == "\r":
                output.append("\n")
                self.after_cr = True
            else:
                if char == "\n":
                    if not self.after_cr:
                        output.append(char)
                elif char == "\t" or (char >= " " and not "\x7f" <= char <= "\x9f"):
                    output.append(char)
                self.after_cr = False
        return "".join(output)


def main() -> None:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    cleaner = LogText()
    try:
        # os.read returns whatever is available, so progress lines without a newline show at once.
        while chunk := os.read(sys.stdin.fileno(), 8192):
            sys.stdout.write(cleaner.feed(decoder.decode(chunk)))
            sys.stdout.flush()
        sys.stdout.write(cleaner.feed(decoder.decode(b"", final=True)))
        sys.stdout.flush()
    except (KeyboardInterrupt, BrokenPipeError):
        pass


if __name__ == "__main__":
    main()
