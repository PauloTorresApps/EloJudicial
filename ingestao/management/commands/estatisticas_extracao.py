"""Estatísticas da extração de texto das ingestões, para calibrar os limiares (docs/0014).

Só contagens e distribuições: nenhum texto, nome de documento, processo ou usuário sai no
relatório. Exemplos:

    python manage.py estatisticas_extracao
    python manage.py estatisticas_extracao --sistema "EPROC TJTO" --desde 2026-12-01
    python manage.py estatisticas_extracao --simular min_caracteres_com_imagem=500 qualidade_minima=0.7
    python manage.py estatisticas_extracao --json
"""

import dataclasses
import json
from collections import Counter, defaultdict
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from ingestao.extracao import LIMIARES, PRECISAM_OCR, Limiares, situacao_da_pagina
from ingestao.models import ArquivoIngerido, PaginaExtraida

FAIXAS_CARACTERES = [(0, 0), (1, 19), (20, 99), (100, 299), (300, 999), (1000, None)]
FAIXAS_QUALIDADE = [(0, 0.3), (0.3, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, None)]
FAIXAS_CONFIANCA = [(0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 0.9), (0.9, 0.95), (0.95, None)]
FAIXAS_IMAGEM = [(0, 0), (1, 99_999), (100_000, 499_999), (500_000, 999_999), (1_000_000, 1_999_999), (2_000_000, None)]


def _faixa(valor, faixas, inteiro=True):
    for inicio, fim in faixas:  # a última faixa é aberta (fim None)
        if fim is None or (valor <= fim if inteiro else valor < fim):
            return _rotulo(inicio, fim, inteiro)


def _rotulo(inicio, fim, inteiro):
    if fim is None:
        return f'{inicio:,}+'.replace(',', '.') if inteiro else f'≥ {inicio}'
    if inteiro:
        return f'{inicio:,}'.replace(',', '.') if inicio == fim else f'{inicio:,}–{fim:,}'.replace(',', '.')
    return f'{inicio}–{fim}'


class Command(BaseCommand):
    help = 'Estatísticas da extração de texto (páginas que precisam de OCR) e simulação de limiares.'

    def add_arguments(self, parser):
        parser.add_argument('--sistema', help='nome do sistema judicial (ex.: "EPROC TJTO")')
        parser.add_argument('--desde', type=date.fromisoformat, help='só lotes criados a partir desta data (AAAA-MM-DD)')
        parser.add_argument('--simular', nargs='+', metavar='LIMIAR=VALOR', default=[],
                            help=f'outros limiares para comparar: {", ".join(f.name for f in dataclasses.fields(Limiares))}')
        parser.add_argument('--json', action='store_true', help='saída em JSON')

    def handle(self, *args, **opcoes):
        simulados = self._limiares_simulados(opcoes['simular'])
        arquivos = ArquivoIngerido.objects.all()
        if opcoes['sistema']:
            arquivos = arquivos.filter(lote__ambiente__sistema__nome__iexact=opcoes['sistema'])
        if opcoes['desde']:
            arquivos = arquivos.filter(lote__criado_em__date__gte=opcoes['desde'])

        relatorio = {
            'limiares': dataclasses.asdict(LIMIARES),
            'arquivos_por_situacao': dict(Counter(arquivos.values_list('situacao', flat=True))),
            'arquivos_por_tipo': dict(Counter(arquivos.exclude(mimetype='').values_list('mimetype', flat=True))),
        }
        paginas, caracteres, qualidade, imagem, transicoes = Counter(), Counter(), Counter(), Counter(), Counter()
        por_sistema = defaultdict(lambda: {'paginas': 0, 'precisam_ocr': 0})
        imagens_avulsas = 0
        por_ocr, confianca = Counter(), Counter()
        consulta = PaginaExtraida.objects.filter(arquivo__in=arquivos).values_list(
            'situacao', 'caracteres', 'qualidade', 'maior_imagem', 'metodo', 'confianca',
            'arquivo__mimetype', 'arquivo__lote__ambiente__sistema__nome',
        )
        for situacao, chars, qual, maior, metodo, conf, mimetype, sistema in consulta.iterator(chunk_size=5000):
            por_sistema[sistema]['paginas'] += 1
            if metodo != PaginaExtraida.Metodo.NATIVO:
                # Lida por OCR: texto e métricas são do OCR, não servem para calibrar os limiares do nativo.
                por_ocr[(metodo, situacao)] += 1
                confianca[(metodo, _faixa(conf or 0, FAIXAS_CONFIANCA, inteiro=False))] += 1
                continue
            por_sistema[sistema]['precisam_ocr'] += situacao in PRECISAM_OCR
            if mimetype.startswith('image/'):
                imagens_avulsas += 1  # sempre vão para OCR; não entram nas distribuições
                continue
            paginas[situacao] += 1
            caracteres[(_faixa(chars, FAIXAS_CARACTERES), maior >= LIMIARES.imagem_grande_pixels)] += 1
            imagem[_faixa(maior, FAIXAS_IMAGEM)] += 1
            if qual is not None and chars >= LIMIARES.min_caracteres:
                qualidade[_faixa(qual, FAIXAS_QUALIDADE, inteiro=False)] += 1
            if simulados:
                transicoes[(situacao, situacao_da_pagina(chars, qual, maior, simulados))] += 1

        relatorio['paginas_por_situacao'] = dict(paginas)
        relatorio['imagens_avulsas'] = imagens_avulsas
        relatorio['paginas_por_sistema'] = dict(sorted(por_sistema.items()))
        relatorio['caracteres_por_pagina'] = {
            rotulo: {'sem_imagem_grande': caracteres[(rotulo, False)], 'com_imagem_grande': caracteres[(rotulo, True)]}
            for rotulo in (_rotulo(i, f, True) for i, f in FAIXAS_CARACTERES)
        }
        relatorio['qualidade_do_texto'] = {_rotulo(i, f, False): qualidade[_rotulo(i, f, False)] for i, f in FAIXAS_QUALIDADE}
        relatorio['maior_imagem_pixels'] = {_rotulo(i, f, True): imagem[_rotulo(i, f, True)] for i, f in FAIXAS_IMAGEM}
        relatorio['ocr'] = {
            'paginas_por_metodo_e_situacao': {f'{m} / {s}': n for (m, s), n in sorted(por_ocr.items())},
            'confianca': {f'{m} / {faixa}': n for (m, faixa), n in sorted(confianca.items())},
        }
        if simulados:
            relatorio['simulacao'] = {
                'limiares': dataclasses.asdict(simulados),
                'mudancas': {f'{de} -> {para}': n for (de, para), n in sorted(transicoes.items()) if de != para},
                'precisam_ocr_atual': sum(n for (de, _), n in transicoes.items() if de in PRECISAM_OCR),
                'precisam_ocr_simulado': sum(n for (_, para), n in transicoes.items() if para in PRECISAM_OCR),
            }

        if opcoes['json']:
            self.stdout.write(json.dumps(relatorio, ensure_ascii=False, indent=2))
        else:
            self._texto(relatorio)

    def _limiares_simulados(self, pares):
        if not pares:
            return None
        nomes = {f.name: f.type for f in dataclasses.fields(Limiares)}
        valores = {}
        for par in pares:
            nome, _, valor = par.partition('=')
            if nome not in nomes or not valor:
                raise CommandError(f'Limiar inválido: {par!r}. Use um de: {", ".join(nomes)}.')
            valores[nome] = float(valor) if nomes[nome] in (float, 'float') else int(valor)
        return dataclasses.replace(LIMIARES, **valores)

    def _texto(self, r):
        escrever = self.stdout.write
        total_paginas = sum(r['paginas_por_situacao'].values()) + r['imagens_avulsas']
        escrever('Limiares atuais: ' + ', '.join(f'{k}={v}' for k, v in r['limiares'].items()))
        escrever('')
        escrever(f'Arquivos: {sum(r["arquivos_por_situacao"].values())}')
        self._tabela(r['arquivos_por_situacao'])
        escrever('Arquivos por tipo:')
        self._tabela(r['arquivos_por_tipo'])
        escrever(f'Páginas com o texto nativo: {total_paginas} (imagens avulsas, sempre para OCR: {r["imagens_avulsas"]})')
        self._tabela(r['paginas_por_situacao'], total=total_paginas)
        escrever('Páginas por sistema (total / precisam de OCR):')
        for sistema, n in r['paginas_por_sistema'].items():
            escrever(f'  {sistema:<30} {n["paginas"]:>8} {n["precisam_ocr"]:>8}')
        escrever('')
        escrever(f'Caracteres por página (sem / com imagem grande ≥ {r["limiares"]["imagem_grande_pixels"]:,} px):'.replace(',', '.'))
        for faixa, n in r['caracteres_por_pagina'].items():
            escrever(f'  {faixa:<12} {n["sem_imagem_grande"]:>8} {n["com_imagem_grande"]:>8}')
        escrever('')
        escrever(f'Qualidade do texto (páginas com ≥ {r["limiares"]["min_caracteres"]} caracteres):')
        self._tabela(r['qualidade_do_texto'])
        escrever('Maior imagem da página (pixels):')
        self._tabela(r['maior_imagem_pixels'])
        if r['ocr']['paginas_por_metodo_e_situacao']:
            escrever('Páginas lidas por OCR (método / situação):')
            self._tabela(r['ocr']['paginas_por_metodo_e_situacao'])
            escrever('Confiança do OCR (método / faixa):')
            self._tabela(r['ocr']['confianca'])
        if 'simulacao' in r:
            s = r['simulacao']
            escrever('Simulação: ' + ', '.join(f'{k}={v}' for k, v in s['limiares'].items()))
            escrever(f'  Páginas que precisam de OCR: {s["precisam_ocr_atual"]} -> {s["precisam_ocr_simulado"]}')
            for mudanca, n in s['mudancas'].items() or {'(nenhuma página mudaria)': ''}.items():
                escrever(f'  {mudanca:<30} {n:>8}')

    def _tabela(self, contagem, total=None):
        total = total or sum(contagem.values()) or 1
        for chave, n in contagem.items():
            self.stdout.write(f'  {chave:<30} {n:>8} {n / total:>7.1%}')
        self.stdout.write('')
