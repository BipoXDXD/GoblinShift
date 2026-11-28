"""Formatos de entrada de cada versão, nos nomes do Pillow; sem o PIL, para o plano importar.

O MPO não entra nas listas: ele vem pela fábrica do JPEG, que devolve um MpoImageFile quando o
arquivo tem o índice de várias imagens. A v1 fica com JPEG e PNG para sempre (1.6); WebP e GIF
estáticos entram só pela v2.
"""

V1_FORMATS = ("JPEG", "PNG")
V2_FORMATS = ("JPEG", "PNG", "WEBP", "GIF")
