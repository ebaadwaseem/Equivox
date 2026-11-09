"""Prepare a streaming Moonshine model once. No microphone access.

python -m pip install -r requirements-voice.txt
python -m scripts.setup_voice --size medium
VB_TRANSCRIBER=moonshine python main.py
"""
import argparse
import json
from pathlib import Path

from audio.moonshine_transcriber import CONFIG_PATH


def main():
    from moonshine_voice import get_model_for_language, ModelArch
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size', choices=['tiny', 'base', 'small', 'medium'], default='medium')
    args = parser.parse_args()
    arch = getattr(ModelArch, f'{args.size.upper()}_STREAMING')
    cache = CONFIG_PATH.parent
    cache.mkdir(parents=True, exist_ok=True)
    model_path, model_arch = get_model_for_language('en', arch, cache_root=cache)
    CONFIG_PATH.write_text(json.dumps(dict(model_path=str(Path(model_path).resolve()), model_arch=int(model_arch)), indent=2))
    print(f'Prepared {args.size} streaming model. Run VB_TRANSCRIBER=moonshine python main.py')


if __name__ == '__main__':
    main()
