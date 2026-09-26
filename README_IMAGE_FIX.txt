Taha's Helper Bot v28 image generation fix

- Uses current Pollinations native GET image endpoint first.
- Does not force width/height on the primary request.
- Falls back to OpenAI-compatible POST with a valid 1024x1024 size.
- Shows a more useful Pollinations error when both attempts fail.
- Keeps the original v28 UI, upload, multi-message, Vision and voice features.
