"""Replay a mono PCM16 WAV through the prepared local streaming model.

python -m scripts.benchmark_voice --wav sample.wav --realtime
No microphone access and no audio uploads. Reports actual partials and final text.
"""
import argparse
import json
import time
import wave


def main():
    import numpy as np
    from moonshine_voice import TranscriptEventListener
    from audio.moonshine_transcriber import load_model
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wav', required=True)
    parser.add_argument('--realtime', action='store_true')
    args=parser.parse_args()
    with wave.open(args.wav) as audio:
        if audio.getsampwidth()!=2 or audio.getnchannels()!=1:
            parser.error('Expected a mono PCM16 WAV')
        rate=audio.getframerate()
        samples=np.frombuffer(audio.readframes(audio.getnframes()),dtype='<i2').astype(np.float32)/32768
    samples=np.concatenate([samples,np.zeros(rate*2,dtype=np.float32)])
    events=[]
    model=load_model()
    native=model.create_stream(update_interval=.16)
    started=time.perf_counter()

    class Listener(TranscriptEventListener):
        def record(self,event,final):
            result=dict(text=event.line.text,final=final,at_ms=round((time.perf_counter()-started)*1000),
                        inference_ms=event.line.last_transcription_latency_ms,line_id=event.line.line_id)
            events.append(result)
            print(json.dumps(result),flush=True)
        def on_line_text_changed(self,event):
            self.record(event,False)
        def on_line_completed(self,event):
            self.record(event,True)
    native.add_listener(Listener())
    try:
        native.start()
        chunk=max(1,int(rate*.032))
        for i in range(0,len(samples),chunk):
            if args.realtime:
                time.sleep(max(0,(i+chunk)/rate-(time.perf_counter()-started)))
            native.add_audio(samples[i:i+chunk],rate)
        native.stop()
        finals=[e['text'] for e in events if e['final']]
        print(json.dumps(dict(audio_seconds=round(len(samples)/rate,2),
                             elapsed_seconds=round(time.perf_counter()-started,2),
                             partial_events=sum(not e['final'] for e in events),finals=finals)))
    finally:
        native.close()
        model.close()


if __name__=='__main__':
    main()
