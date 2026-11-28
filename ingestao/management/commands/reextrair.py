"""Reaplica a extração de texto aos originais já guardados, sem consultar o tribunal (docs/0014).

Usado depois de mudar os limiares ou a extração: reclassifica as páginas e atualiza a situação
dos arquivos. Exemplos:

    python manage.py reextrair --todos
    python manage.py reextrair --lote 12 15
    python manage.py reextrair --todos --situacao parcial sem_texto
    python manage.py reextrair --todos --situacao reconhecendo   # arquivo preso no OCR (fila perdida)
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from ingestao.models import ArquivoIngerido
from ingestao.tasks import extrair_e_salvar


class Command(BaseCommand):
    help = 'Reaplica a extração de texto aos arquivos já ingeridos (a partir do original guardado).'

    def add_arguments(self, parser):
        alvo = parser.add_mutually_exclusive_group(required=True)
        alvo.add_argument('--lote', nargs='+', type=int, metavar='ID', help='ids dos lotes')
        alvo.add_argument('--todos', action='store_true', help='todos os arquivos já concluídos')
        parser.add_argument('--situacao', nargs='+', choices=(*ArquivoIngerido.CONCLUIDOS, ArquivoIngerido.Situacao.RECONHECENDO),
                            help='só arquivos nestas situações (padrão: todas as concluídas)')

    def handle(self, *args, **opcoes):
        arquivos = ArquivoIngerido.objects.filter(situacao__in=opcoes['situacao'] or ArquivoIngerido.CONCLUIDOS)
        if opcoes['lote']:
            arquivos = arquivos.filter(lote__in=opcoes['lote'])
        arquivos = arquivos.exclude(arquivo='')
        if not arquivos.exists():
            raise CommandError('Nenhum arquivo concluído com original guardado para reprocessar.')

        mudancas, falhas, total = Counter(), 0, 0
        for arquivo in arquivos.iterator(chunk_size=200):
            antes = arquivo.situacao
            try:
                with arquivo.arquivo.open('rb') as f:
                    dados = f.read()
            except OSError as exc:
                falhas += 1
                self.stderr.write(f'Arquivo #{arquivo.pk}: original não encontrado ({exc}).')
                continue
            extrair_e_salvar(arquivo, dados, reaproveitar=False)  # o OCR lê de novo, não copia o antigo
            total += 1
            if arquivo.situacao != antes:
                mudancas[(antes, arquivo.situacao)] += 1

        self.stdout.write(f'{total} arquivo(s) reprocessado(s); {sum(mudancas.values())} mudaram de situação.')
        for (antes, depois), n in sorted(mudancas.items()):
            self.stdout.write(f'  {antes} -> {depois}: {n}')
        if falhas:
            self.stdout.write(f'{falhas} arquivo(s) sem o original no armazenamento.')
