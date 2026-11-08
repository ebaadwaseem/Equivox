
"""Offline English microphone transcription for macOS and Windows."""
import argparse
from array import array
import json
import math
from pathlib import Path
import queue
import signal
import sys
import threading
import time


# Change this path if you want to use a different model with your IDE's Run button.
DEFAULT_MODEL = Path(__file__).resolve().parent / 'vosk-model-small-en-us-0.15'
# Audio energy threshold, not a speech detector. Adjust for your microphone.
SIGNAL_THRESHOLD_DBFS = -45.0
NO_AUDIO_TIMEOUT = 3.0


class InputMonitor:
    """Report audio delivery separately from recognition output."""
    def __init__(self, display=None):
        self.display = display
        self.last_signal = float('-inf')
        self.last_text = float('-inf')
        self.last_report = float('-inf')
        self.peak_dbfs = -120.0

    def audio_received(self, data, now):
        samples = array('h')
        samples.frombytes(data)
        if not samples:
            return
        rms = math.sqrt(sum(float(value) ** 2 for value in samples) / len(samples))
        dbfs = 20 * math.log10(max(rms / 32768, 1e-6))
        self.peak_dbfs = max(self.peak_dbfs, dbfs)
        if dbfs >= SIGNAL_THRESHOLD_DBFS:
            self.last_signal = now

    def text_received(self, text, now):
        if text:
            self.last_text = now

    def report(self, now):
        if now - self.last_report < 1.0:
            return
        self.last_report = now
        if now - self.last_text < 2.0:
            recognition = 'TEXT_DETECTED'
        elif now - self.last_signal < 1.0:
            recognition = 'PROCESSING_AUDIO (no words detected yet)'
        else:
            recognition = 'WAITING_FOR_SPEECH'
        level = 'SIGNAL_DETECTED' if now - self.last_signal < 1.0 else 'QUIET_OR_MUTED'
        message = (f'Microphone: RECEIVING_AUDIO | Input: {level} | '
                   f'Level: {self.peak_dbfs:.1f} dBFS | Recognition: {recognition}')
        print('[status] ' + message, flush=True)
        if self.display is not None:
            self.display.status(message)
        self.peak_dbfs = -120.0


def device_value(value):
    return int(value) if value.isdecimal() else value


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list-devices', action='store_true')
    parser.add_argument('--device', type=device_value, help='Input ID or name substring')
    parser.add_argument('--model', type=Path, default=DEFAULT_MODEL,
                        help='English model directory; defaults to vosk-model-small-en-us-0.15 beside this script')
    parser.add_argument('--block-ms', type=int, default=100, help='Audio block duration (20-500 ms)')
    parser.add_argument('--samplerate', type=int, help='Default: microphone native sample rate')
    args = parser.parse_args()
    if not 20 <= args.block_ms <= 500:
        parser.error('--block-ms must be between 20 and 500')
    if args.samplerate is not None and args.samplerate <= 0:
        parser.error('--samplerate must be positive')
    return args


class Display:
    def __init__(self):
        self.previous = ''

    def partial(self, text):
        if text != self.previous:
            self.previous = text
            if text:
                print('[partial] ' + text, flush=True)

    def final(self, text):
        self.previous = ''
        if text:
            print('[final] ' + text, flush=True)


def run(args, display=None, stop=None):
    try:
        import sounddevice as sd
        from vosk import KaldiRecognizer, Model, SetLogLevel
    except ImportError as exc:
        raise RuntimeError('Install dependencies: python -m pip install -r requirements.txt') from exc
    if args.list_devices:
        print(sd.query_devices())
        return
    if not args.model.is_dir():
        raise RuntimeError(
            f'Model directory not found: {args.model}\n'
            f'Extract vosk-model-small-en-us-0.15.zip beside this script: {DEFAULT_MODEL.parent}\n'
            'Then click Run again. See README.md for the download link.')
    SetLogLevel(-1)
    print('Loading model...', file=sys.stderr)
    model = Model(str(args.model.resolve()))
    info = sd.query_devices(args.device, 'input')
    rate = args.samplerate or int(info['default_samplerate'])
    sd.check_input_settings(device=args.device, channels=1, dtype='int16', samplerate=rate)
    recognizer = KaldiRecognizer(model, rate)
    audio = queue.Queue(maxsize=max(2, 2000 // args.block_ms))
    overloaded = threading.Event()
    overflowed = threading.Event()
    stop = stop if stop is not None else threading.Event()
    last_callback = time.monotonic()
    monitor = InputMonitor(display)

    def capture(data, frames, timing, status):
        nonlocal last_callback
        if stop.is_set():
            return
        last_callback = time.monotonic()
        if status:
            overflowed.set()
        try:
            audio.put_nowait(bytes(data))
        except queue.Full:
            overloaded.set()

    def request_stop(signum, frame):
        stop.set()

    def wait_for_enter():
        try:
            # EOF means interactive input is unavailable, not a stop request.
            if sys.stdin.readline():
                stop.set()
        except (OSError, ValueError):
            pass

    display = display if display is not None else Display()
    stream = sd.RawInputStream(device=args.device, samplerate=rate, channels=1,
                               dtype='int16', blocksize=round(rate * args.block_ms / 1000),
                               latency='low', callback=capture)
    previous_handlers = {}
    try:
        # Explicit signal handlers avoid relying on a blocking queue being interrupted.
        # Install only on the main thread, as required by Python.
        if threading.current_thread() is threading.main_thread():
            for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
                sig = getattr(signal, name, None)
                if sig is not None:
                    previous_handlers[sig] = signal.signal(sig, request_stop)
        stream.start()
        last_callback = time.monotonic()
        print(f'Listening: {info["name"]}, {rate} Hz. Close the subtitle window or press Enter to stop.', file=sys.stderr)
        print('[status] Microphone: OPEN | Recognition: READY | Waiting for audio...', flush=True)
        threading.Thread(target=wait_for_enter, daemon=True).start()
        while not stop.is_set():
            if not stream.active:
                raise RuntimeError('Microphone stream stopped. Check the device connection.')
            if time.monotonic() - last_callback > NO_AUDIO_TIMEOUT:
                raise RuntimeError('Microphone: NO_AUDIO_DATA for 3 seconds. Check the device, permissions, and audio driver.')
            if overloaded.is_set() or overflowed.is_set():
                raise RuntimeError('Audio capture fell behind. Try --block-ms 200, a smaller model, or close CPU-heavy apps. Some audio may be missing.')
            try:
                data = audio.get(timeout=0.05)
            except queue.Empty:
                if not stream.active:
                    raise RuntimeError('Microphone stream stopped. Check the device connection.')
                continue
            if stop.is_set():
                break
            now = time.monotonic()
            monitor.audio_received(data, now)
            if recognizer.AcceptWaveform(data):
                text = json.loads(recognizer.Result()).get('text', '')
                display.final(text)
            else:
                text = json.loads(recognizer.PartialResult()).get('partial', '')
                display.partial(text)
            now = time.monotonic()
            monitor.text_received(text, now)
            monitor.report(now)
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()
        try:
            # Abort rather than wait for pending audio. Do not drain the queue on exit.
            try:
                stream.abort()
            finally:
                stream.close()
            display.final(json.loads(recognizer.FinalResult()).get('text', ''))
            print('[status] Microphone: CLOSED | Recognition: STOPPED', flush=True)
        finally:
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)


def main():
    args = arguments()
    if args.list_devices:
        try:
            run(args)
        except Exception as exc:
            print(f'Error: {exc}', file=sys.stderr)
            return 1
        return 0
    from speech_display import LiveSubtitle
    popup = LiveSubtitle()
    errors = []

    def recognize():
        try:
            run(args, display=popup, stop=popup.closed)
        except Exception as exc:
            errors.append(exc)
            print(f'Error: {exc}', file=sys.stderr, flush=True)
            popup.status(f'Error: {exc}')
        else:
            popup.status('Microphone: CLOSED | Recognition: STOPPED')

    worker = threading.Thread(target=recognize, daemon=True)
    worker.start()
    try:
        popup.run()
    except KeyboardInterrupt:
        popup.close()
        return 130
    finally:
        popup.closed.set()
        worker.join(timeout=5)
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
