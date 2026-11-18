"""Chat readers. Each reader runs in a thread and calls on_message(platform, channel, t, user, text).

- TwitchIRCReader: real. Anonymous read-only IRC, no token needed.
- YouTubeChatReader / KickChatReader: stubs with the same interface.
"""
from __future__ import annotations

import logging
import random
import socket
import threading
import time
from typing import Callable

log = logging.getLogger("clipbot.chat")

OnMessage = Callable[[str, str, float, str, str], None]


class TwitchIRCReader(threading.Thread):
    HOST, PORT = "irc.chat.twitch.tv", 6667

    def __init__(self, channels: list[str], on_message: OnMessage, stop: threading.Event):
        super().__init__(daemon=True, name="twitch-irc")
        self.channels = [c.lower().lstrip("#") for c in channels]
        self.on_message = on_message
        self.stop_evt = stop

    def run(self):
        backoff = 2
        while not self.stop_evt.is_set():
            try:
                self._session()
                backoff = 2
            except Exception as e:  # network blips, Twitch RECONNECT, etc.
                log.warning("twitch irc dropped: %s; reconnecting in %ss", e, backoff)
                self.stop_evt.wait(backoff)
                backoff = min(backoff * 2, 60)

    def _session(self):
        nick = f"justinfan{random.randint(10000, 99999)}"  # anonymous read-only login
        with socket.create_connection((self.HOST, self.PORT), timeout=30) as s:
            s.sendall(f"NICK {nick}\r\n".encode())
            # Twitch limits JOINs (~20 per 10 s), so stagger them
            for i, ch in enumerate(self.channels):
                s.sendall(f"JOIN #{ch}\r\n".encode())
                if i % 15 == 14:
                    time.sleep(10)
            log.info("twitch irc joined: %s", ", ".join(self.channels))
            buf = b""
            while not self.stop_evt.is_set():
                try:
                    data = s.recv(65536)
                except socket.timeout:
                    s.sendall(b"PING :keepalive\r\n")
                    continue
                if not data:
                    raise ConnectionError("socket closed")
                buf += data
                *lines, buf = buf.split(b"\r\n")
                for raw in lines:
                    self._handle(s, raw.decode("utf-8", "replace"))

    def _handle(self, s, line: str):
        if line.startswith("PING"):
            s.sendall(("PONG" + line[4:] + "\r\n").encode())
            return
        if " RECONNECT" in line:
            raise ConnectionError("server asked to reconnect")
        # :user!user@user.tmi.twitch.tv PRIVMSG #channel :message text
        if " PRIVMSG #" not in line or not line.startswith(":"):
            return
        try:
            prefix, rest = line[1:].split(" PRIVMSG #", 1)
            channel, text = rest.split(" :", 1)
            user = prefix.split("!", 1)[0]
        except ValueError:
            return
        self.on_message("twitch", channel, time.time(), user, text)


class _StubReader(threading.Thread):
    platform = "stub"

    def __init__(self, channels, on_message: OnMessage, stop: threading.Event):
        super().__init__(daemon=True, name=f"{self.platform}-chat")
        self.channels = channels

    def run(self):
        log.warning("%s chat reader is a stub; channels %s are not being read", self.platform, self.channels)


class YouTubeChatReader(_StubReader):
    """TODO: YouTube Data API liveChatMessages.list (quota-heavy) or a scraper like pytchat."""
    platform = "youtube"


class KickChatReader(_StubReader):
    """TODO: Kick's (undocumented) Pusher websocket for chatroom events."""
    platform = "kick"


READERS = {"twitch": TwitchIRCReader, "youtube": YouTubeChatReader, "kick": KickChatReader}
