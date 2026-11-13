"""Gera a marca do EloJudicial em "brush stroke line art".

Desenho: dois elos de corrente entrelaçados formando o travessão de uma balança.
Cada traço é uma linha-guia convertida em contorno preenchido, com espessura
variável (pressão do pincel) e pontas afiladas.

Uso (na raiz do projeto):  python scripts/gerar_logo.py
Escreve templates/layouts/_logo.html e core/static/elojudicial/{logo-claro,logo-escuro,favicon}.svg.
"""
import math
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DOURADO = '#93753b'                 # sobre fundo claro
DOURADO_SOBRE_MARINHO = '#d3b36e'   # sobre fundo escuro
MARINHO = '#162e4d'
PESO = 1.0  # 1.0 = traço normal; o favicon usa um peso maior para não sumir em 16px.

def line(p0, p1, n=20):
    return [(p0[0]+(p1[0]-p0[0])*i/n, p0[1]+(p1[1]-p0[1])*i/n) for i in range(n+1)]

def cubic(p0, c1, c2, p1, n=40):
    out = []
    for i in range(n+1):
        t = i/n; u = 1-t
        out.append((u**3*p0[0]+3*u*u*t*c1[0]+3*u*t*t*c2[0]+t**3*p1[0],
                    u**3*p0[1]+3*u*u*t*c1[1]+3*u*t*t*c2[1]+t**3*p1[1]))
    return out

def arc(c, r, a0, a1, n=30):
    return [(c[0]+r*math.cos(math.radians(a0+(a1-a0)*i/n)), c[1]+r*math.sin(math.radians(a0+(a1-a0)*i/n))) for i in range(n+1)]

def chain(*segs):
    pts = []
    for s in segs:
        pts += s if not pts else s[1:]
    return pts

def taper(t, a=0.18, b=0.18, k=0.65, minimo=0.08):
    f = 1.0
    if t < a: f *= (t/a)**k
    if t > 1-b: f *= ((1-t)/b)**k
    return max(f, minimo)

def stroke(pts, width):
    """width(t, x, y) -> espessura total. Retorna contorno preenchido."""
    d = [0.0]
    for i in range(1, len(pts)):
        d.append(d[-1] + math.dist(pts[i-1], pts[i]))
    L = d[-1]
    esq, dir_ = [], []
    for i, (x, y) in enumerate(pts):
        a = pts[max(i-1, 0)]; b = pts[min(i+1, len(pts)-1)]
        dx, dy = b[0]-a[0], b[1]-a[1]; n = math.hypot(dx, dy) or 1
        nx, ny = -dy/n, dx/n
        w = width(d[i]/L, x, y)*PESO/2
        esq.append((x+nx*w, y+ny*w)); dir_.append((x-nx*w, y-ny*w))
    contorno = esq + dir_[::-1]
    return 'M' + 'L'.join(f'{x:.1f} {y:.1f}' for x, y in contorno) + 'Z'

def pressao(lo, hi, y0, y1):
    return lambda y: lo + (hi-lo)*min(max((y-y0)/(y1-y0), 0), 1)

def desenhar():
    paths = []

    # 1) Elo horizontal (travessão): passa POR BAIXO do elo vertical no alto; mais fino em cima, cheio embaixo.
    p = pressao(2.1, 3.8, 14, 26)
    anel = chain(line((22, 14), (12, 14), 10), arc((12, 20), 6, -90, -270, 30),
                 line((12, 26), (52, 26), 40), arc((52, 20), 6, 90, -90, 30), line((52, 14), (42, 14), 10))
    paths.append(stroke(anel, lambda t, x, y: p(y)*taper(t, .07, .07)))

    # 2) Elo vertical, parte de cima (passa POR CIMA do travessão).
    p2 = pressao(3.4, 2.2, 6, 23)
    alto = chain(line((26, 23), (26, 11), 14), arc((32, 11), 6, 180, 360, 30), line((38, 11), (38, 23), 14))
    paths.append(stroke(alto, lambda t, x, y: p2(y)*taper(t, .12, .12)))

    # 3) Elo vertical, parte de baixo (sai de trás do travessão e vira o pilar).
    fundo = chain(line((26, 29.2), (26, 28), 3), arc((32, 28), 6, 180, 0, 30), line((38, 28), (38, 29.2), 3))
    paths.append(stroke(fundo, lambda t, x, y: 3.6*taper(t, .25, .25, .8)))

    # 4) Pilar: afina em cima, encorpa embaixo.
    paths.append(stroke(cubic((32, 33.5), (32.4, 40), (31.6, 47), (32, 54.5)), lambda t, x, y: (2.2+1.6*t)*taper(t, .04, .03, .6, .7)))

    # 5) Base: pincelada horizontal levemente arqueada, pontas afiladas.
    paths.append(stroke(cubic((19.5, 57.5), (26, 55), (38, 55), (44.5, 57.5)), lambda t, x, y: 4.0*taper(t, .35, .35, .7)))

    # 6) Cordas dos pratos: traços finos (hairline), afinando no alto.
    for ax in (12, 52):
        for bx in (ax-7, ax+7):
            paths.append(stroke(line((ax, 26.5), (bx, 41), 20), lambda t, x, y: 1.25*taper(t, .3, .02, .7, .25)))

    # 7) Pratos: crescentes (cheios no fundo, pontas afiladas) + borda fina.
    for cx in (12, 52):
        paths.append(stroke(cubic((cx-9, 40.5), (cx-8, 51.5), (cx+8, 51.5), (cx+9, 40.5)), lambda t, x, y: 4.2*taper(t, .45, .45, .9)))
        paths.append(stroke(line((cx-9, 41), (cx+9, 41), 20), lambda t, x, y: 1.1*taper(t, .15, .15, .6)))
    return ' '.join(paths)


def main():
    global PESO
    PESO = 1.0
    d = desenhar()
    PESO = 1.45
    d_favicon = desenhar()

    (RAIZ / 'templates/layouts/_logo.html').write_text(
        '{% comment %}Marca do EloJudicial em traço de pincel: dois elos entrelaçados formando uma balança. '
        'Herda a cor do texto (currentColor). Parâmetro opcional: tamanho (px). '
        'Gerado por scripts/gerar_logo.py: não edite à mão.{% endcomment %}\n'
        '<svg viewBox="0 0 64 64" width="{{ tamanho|default:24 }}" height="{{ tamanho|default:24 }}" '
        f'fill="currentColor" class="elo-selo shrink-0" role="img" aria-label="EloJudicial"><path d="{d}"/></svg>\n')

    static = RAIZ / 'core/static/elojudicial'
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"{}>{}</svg>\n'
    (static / 'logo-claro.svg').write_text(svg.format(f' fill="{DOURADO}"', f'<path d="{d}"/>'))
    (static / 'logo-escuro.svg').write_text(svg.format(f' fill="{DOURADO_SOBRE_MARINHO}"', f'<path d="{d}"/>'))
    (static / 'favicon.svg').write_text(svg.format('', (
        f'<rect width="64" height="64" rx="14" fill="{MARINHO}"/>'
        f'<path transform="translate(6 6) scale(.8125)" fill="{DOURADO_SOBRE_MARINHO}" d="{d_favicon}"/>')))


if __name__ == '__main__':
    main()
