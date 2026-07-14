# Runtime data

This directory is the host-side runtime volume layout used by the Compose
services. Only this documentation and `.gitkeep` directory placeholders are
tracked. Runtime files remain ignored by the repository-level `.gitignore`.

- `config/`: local `youdub.json`; may contain API tokens and SSH settings.
- `cookies/`: browser cookies used by yt-dlp.
- `tasks/`: mutable task state.
- `videos/`: downloaded media and generated artifacts.
- `logs/`: runtime logs.
- `samples/`: optional local smoke-test media and metadata.
- `cache/huggingface/`: Hugging Face and persistent pyannote model caches.
- `cache/nltk/`: NLTK data used by the WhisperX dependency chain.
- `cache/torch/`: Torch checkpoints and caches.

Initialize a local configuration with:

```bash
cp config.example.json data/config/youdub.json
```

Never force-add the generated contents of these directories. In particular,
do not commit `youdub.json`, `cookies.txt`, `tasks.json`, media, model weights,
or logs.
