"""Compare local ASR and selected redubs on existing tasks without changing them.

Run in the GPU container with --output /data/videos/_experiments/tts-redub-<date>.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from youdub.tts import TTSConfig, load_voxcpm_model, unload_voxcpm_model, tts_synthesis_text, audio_duration_ms, choose_fallback_reference
from youdub.tts_redub import RedubTTSConfig, redub_tts, load_redub_scorer, score_redub_audio
from youdub.tts_quality import TTSQualityConfig, inspect_tts_quality


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/data/videos'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--device', default='cuda:1')
    parser.add_argument('--per-task', type=int, default=6)
    parser.add_argument('--control', action='store_true', help='Also try up to 3 candidates with the original parameters')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    # Copy only metadata first; quality inspection writes exclusively into the experiment.
    for translation in sorted(args.root.glob('*/*/translation.json')):
        original = translation.parent
        if not (original / 'subtitles.segments.json').exists():
            continue
        sample = args.output / original.parent.name
        sample.mkdir()
        for name in ('translation.json', 'audio_tts.timings.json', 'subtitles.segments.json'):
            shutil.copy2(original / name, sample / name)
        report = json.loads(inspect_tts_quality(sample, TTSQualityConfig()).read_text())
        hard = [s for s in report['segments'] if s['severity'] == 'hard']
        # Evenly sample the hard cases across the task instead of only its beginning.
        count = min(args.per_task, len(hard))
        selected = [hard[i * len(hard) // count] for i in range(count)] if count else []
        entries = json.loads((original / 'translation.json').read_text())
        entries = entries.get('translation', []) if isinstance(entries, dict) else entries
        subset = []
        plan = []
        elapsed = 0.0
        for index, segment in enumerate(selected, 1):
            old_index = segment['tts_index']
            entry = entries[old_index - 1].copy()
            duration = float(entry['end']) - float(entry['start'])
            entry.update(start=elapsed, end=elapsed + duration, segment_id=index - 1)
            elapsed += duration + 0.3
            subset.append(entry)
            plan.append({**segment, 'tts_index': index, 'segment_id': index - 1, 'original_tts_index': old_index})
            for kind in ('tts', 'vocals'):
                folder = sample / 'segments' / kind
                folder.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original / 'segments' / kind / f'{old_index:04d}.wav', folder / f'{index:04d}.wav')
        (sample / 'translation.json').write_text(json.dumps(subset, ensure_ascii=False, indent=2))
        (sample / 'tts.redub.plan.json').write_text(json.dumps({'segments': plan}, ensure_ascii=False, indent=2))
        if not selected:
            continue
        print(f'Testing {original}: {len(selected)} / {len(hard)} hard segments', flush=True)
        config = TTSConfig(device=args.device, align_audio=False, cache_model=args.control)
        redub_tts(sample, config, RedubTTSConfig())
        history = [json.loads(line) for line in (sample / 'tts.redub.history.jsonl').read_text().splitlines()]
        if args.control:
            import soundfile as sf
            scorer = load_redub_scorer(config)
            for index, record in enumerate(history, 1):
                if record['old_quality']['similarity'] >= 0.9:
                    continue
                model = load_voxcpm_model(config)
                control = []
                reference = sample / 'segments/vocals' / f'{index:04d}.wav'
                if audio_duration_ms(reference) < config.min_reference_ms:
                    reference = choose_fallback_reference(sample / 'segments/vocals', config.min_reference_ms)
                for candidate in range(3):
                    path = sample / 'segments' / f'control-{index:04d}-{candidate + 1}.wav'
                    wav = model.generate(text=tts_synthesis_text(subset[index - 1], config),
                                         reference_wav_path=str(reference),
                                         cfg_value=config.cfg_value, inference_timesteps=config.inference_timesteps)
                    sf.write(str(path), wav, int(model.tts_model.sample_rate))
                    quality = score_redub_audio(scorer, path, tts_synthesis_text(subset[index - 1], config))
                    control.append({'file': str(path), 'quality': quality})
                    if quality['similarity'] >= 0.9:
                        break
                record['control_candidates'] = control
                record['control_best_similarity'] = max([record['old_quality']['similarity']] +
                                                         [c['quality']['similarity'] for c in control])
            del scorer
            unload_voxcpm_model()
        for segment, record in zip(selected, history):
            results.append({**record, 'task': str(original), 'sample_tts_index': record['tts_index'],
                            'tts_index': segment['tts_index'], 'segment_id': segment['segment_id'],
                            'translation': segment['translation'], 'global_similarity': segment['similarity']})
        (args.output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print([(s['tts_index'], r['old_quality']['similarity'], r['new_quality']['similarity'], r['selection_reason'])
               for s, r in zip(selected, history)], flush=True)


if __name__ == '__main__':
    main()
