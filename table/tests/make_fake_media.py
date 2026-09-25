"""Build the fake camera and fake microphone Chrome plays into the pages in browser_e2e.cjs.

camera.y4m : 1280x720 @ 10 fps — blank, then each card held up for 2.5 s with 2 s of blank between,
             the way a card is shown to the laptop camera and taken away.
mic.wav    : 48 kHz mono — table talk with 3 s of silence between lines.
Uses the fixtures e2e_offline.py builds (run that once first).
"""
import subprocess
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"
SHOW = ["Island", "SolRing", "Mulldrifter", "LightningBolt"]     # Bolt: not in the deck, must be rejected
TALK = ["ask_talrand", "pizza", "deal_krenko", "sam_play"]


def run(*a):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *a], check=True)


def main():
    segs = []
    blank = FIX / "seg_blank.y4m"
    run("-f", "lavfi", "-i", "color=c=0x202020:s=1280x720:r=10", "-t", "2", "-pix_fmt", "yuv420p", str(blank))
    segs.append(blank)
    for card in SHOW:
        seg = FIX / f"seg_{card}.y4m"
        run("-loop", "1", "-i", str(FIX / f"{card}.jpg"), "-f", "lavfi", "-i", "color=c=0x202020:s=1280x720:r=10",
            "-filter_complex", "[0:v]scale=-2:640[c];[1:v][c]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p",
            "-t", "2.5", "-r", "10", str(seg))
        segs += [seg, blank]
    lst = FIX / "segs.txt"
    lst.write_text("".join(f"file '{s.name}'\n" for s in segs))
    run("-f", "concat", "-safe", "0", "-i", str(lst), "-pix_fmt", "yuv420p", str(FIX / "camera.y4m"))

    parts = [FIX / "sil3.wav"]
    run("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "3", "-sample_fmt", "s16", str(parts[0]))
    for k in TALK:
        up = FIX / f"{k}_48k.wav"
        run("-i", str(FIX / f"{k}.wav"), "-ar", "48000", "-ac", "1", "-sample_fmt", "s16", str(up))
        parts += [up, FIX / "sil3.wav"]
    alst = FIX / "audio.txt"
    alst.write_text("".join(f"file '{p.name}'\n" for p in parts))
    run("-f", "concat", "-safe", "0", "-i", str(alst), "-ar", "48000", "-ac", "1", "-sample_fmt", "s16",
        str(FIX / "mic.wav"))
    print("built", FIX / "camera.y4m", FIX / "mic.wav")


if __name__ == "__main__":
    main()
