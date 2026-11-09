"""Local streaming speech through Moonshine, with the same contract as Apple Speech.

Audio capture stays off the asyncio and inference threads. Partials retain their
physical utterance id through the final. Imports are lazy so typed/Apple mode has
no Moonshine dependency and startup never downloads a model.
"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import queue
import threading
import time

from shared.protocol import Transcript

CONFIG_PATH = Path(__file__).resolve().parents[1] / '.cache/moonshine/config.json'


def is_available():
    return importlib.util.find_spec('moonshine_voice') is not None and CONFIG_PATH.is_file()


def load_model():
    from moonshine_voice import ModelArch, Transcriber
    config = json.loads(CONFIG_PATH.read_text())
    keyterms = ["Vox", "Equivox", "Edmonton", "physiotherapy", "backspace", "option one", "option two"]
    return Transcriber(config['model_path'], ModelArch(config['model_arch']),
                       update_interval=.3, options={'keyterms': ','.join(keyterms)})


class MoonshineTranscriber:
    def __init__(self, on_level=None, on_status=None):
        self.on_level, self.on_status = on_level, on_status
        self._paused = False
        self._audio = queue.Queue(maxsize=64)
        self._stop = object()
        self._ids = {}
        self._serial = 0
        self._epoch = 0

    def transcript(self, line, final=False):
        key = (self._epoch, line.line_id)
        if key not in self._ids:
            self._serial += 1
            self._ids[key] = self._serial
            if len(self._ids) > 128:
                self._ids.pop(next(iter(self._ids)))
        return Transcript(line.text.strip(), final, id=self._ids[key])

    async def pause(self):
        self._paused = True

    async def resume(self):
        if self._paused:
            await asyncio.to_thread(self._audio.put, ('resume', None))

    async def stream(self):
        if not is_available():
            if self.on_status:
                await self.on_status('error', 'Moonshine is not prepared. Run python -m scripts.setup_voice first.')
            return
        import numpy as np
        import sounddevice as sd
        from moonshine_voice import TranscriptEventListener
        loop = asyncio.get_running_loop()
        events = asyncio.Queue()
        accepting = True
        stop_worker = threading.Event()
        recovering = threading.Event()
        worker = capture = native_stream = model = None
        owner = self

        def emit(kind, value):
            if accepting:
                loop.call_soon_threadsafe(events.put_nowait, (kind, value))

        class Listener(TranscriptEventListener):
            def on_line_text_changed(self, event):
                if event.line.text.strip() and not owner._paused and not recovering.is_set():
                    emit('transcript', (owner._epoch, owner.transcript(event.line)))

            def on_line_completed(self, event):
                if event.line.text.strip() and not owner._paused and not recovering.is_set():
                    emit('transcript', (owner._epoch, owner.transcript(event.line, final=True)))

            def on_error(self, event):
                emit('error', str(event))

        try:
            if self.on_status:
                await self.on_status('loading', 'Loading Moonshine streaming speech model')
            model = await asyncio.to_thread(load_model)
            native_stream = model.create_stream(update_interval=float(os.getenv('VB_ASR_UPDATE_INTERVAL', '.3')))
            native_stream.add_listener(Listener())
            native_stream.start()
            device = os.getenv('VB_AUDIO_DEVICE')
            if device and device.isdigit():
                device = int(device)
            device_info = sd.query_devices(device, 'input')
            rate = int(device_info['default_samplerate'])
            last_level = 0.0

            def callback(data, frames, timing, status):
                nonlocal last_level
                if self._paused or stop_worker.is_set() or recovering.is_set():
                    return
                samples = data[:, 0].copy()
                now = time.monotonic()
                if now - last_level >= .1:
                    last_level = now
                    emit('level', min(1., float(np.sqrt(np.mean(samples * samples))) * 8))
                if status:
                    emit('status', str(status))
                try:
                    self._audio.put_nowait(('audio', samples))
                except queue.Full:
                    recovering.set()

            def process_audio():
                try:
                    deferred = None
                    while not stop_worker.is_set():
                        if recovering.is_set():
                            # Discard the incomplete turn explicitly, then keep the
                            # microphone usable instead of permanently stopping it.
                            while True:
                                try:
                                    self._audio.get_nowait()
                                except queue.Empty:
                                    break
                            deferred = None
                            native_stream.stop()
                            self._epoch += 1
                            native_stream.start()
                            recovering.clear()
                            emit('recovery', 'Audio fell behind. Listening has resumed; please repeat the command. Try the small model if this repeats.')
                        try:
                            item = deferred if deferred is not None else self._audio.get(timeout=.2)
                            deferred = None
                        except queue.Empty:
                            continue
                        if item is self._stop:
                            break
                        kind, samples = item
                        if kind == 'resume':
                            native_stream.stop()
                            self._epoch += 1
                            native_stream.start()
                            self._paused = False
                        elif not self._paused:
                            # One inference pass for accumulated audio, rather than
                            # one per old 32 ms block after a slow decode.
                            chunks = [samples]
                            while True:
                                try:
                                    following = self._audio.get_nowait()
                                except queue.Empty:
                                    break
                                if following is self._stop or following[0] != 'audio':
                                    deferred = following
                                    break
                                chunks.append(following[1])
                            native_stream.add_audio(chunks[0] if len(chunks)==1 else np.concatenate(chunks), rate)
                except Exception as exc:
                    emit('error', str(exc))
                finally:
                    native_stream.stop()

            worker = threading.Thread(target=process_audio, name='EquivoxSpeech', daemon=True)
            worker.start()
            capture = sd.InputStream(samplerate=rate, channels=1, dtype='float32',
                                     blocksize=max(256, int(rate * .032)), device=device, callback=callback)
            capture.start()
            if self.on_status:
                await self.on_status('listening', 'Moonshine streaming speech is ready')
            while True:
                kind, value = await events.get()
                if kind == 'transcript':
                    epoch, transcript = value
                    if epoch == self._epoch and not self._paused:
                        yield transcript
                elif kind == 'level' and self.on_level:
                    await self.on_level(value)
                elif kind == 'error':
                    raise RuntimeError(value)
                elif kind == 'recovery' and self.on_status:
                    await self.on_status('error', value)
                elif kind == 'status' and self.on_status:
                    await self.on_status('audio', value)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.on_status:
                await self.on_status('error', f'Moonshine stopped: {exc}. Typed input remains available.')
        finally:
            accepting = False
            if capture:
                await asyncio.to_thread(capture.close)
            stop_worker.set()
            if worker:
                await asyncio.to_thread(worker.join)
            # Join inference before releasing the native model to avoid use-after-free.
            if native_stream:
                native_stream.close()
            if model:
                model.close()
