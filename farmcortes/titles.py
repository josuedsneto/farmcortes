"""Stage 3 interface: replace this simple generator with Nemotron later."""
import re

def generate(context):
    streamer = context.get('streamer', 'Streamer')
    excerpt = re.sub(r'\s+', ' ', context.get('transcript', '')).strip()[:55].strip(' .,!?')
    return [f'{streamer} no GTA: {excerpt}' if excerpt else f'{streamer} surpreende no GTA', f'O momento inesperado de {streamer} no GTA', f'Olha o que aconteceu com {streamer} no GTA']
