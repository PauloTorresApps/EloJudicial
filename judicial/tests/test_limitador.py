"""Limitador das requisições aos tribunais: uma por vez por tribunal e intervalo mínimo entre elas."""

from unittest import mock

from django.conf import settings

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from judicial import limitador, mni, wsdl
from judicial.limitador import TribunalOcupado, chave_tribunal, paciencia, vez_do_tribunal

URL_1G = 'https://eproc1g-ws.tjto.jus.br/eprocV2_prod_1grau/ws/controlador_ws.php'
URL_HMLG = 'https://eproc2g.hmlg.tjto.jus.br/eproc2g-homolog/ws/controlador_ws.php'


@override_settings(MNI_INTERVALO=2)
class LimitadorTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_hosts_do_mesmo_tribunal_contam_juntos(self):
        self.assertEqual(chave_tribunal(URL_1G), 'tjto.jus.br')
        self.assertEqual(chave_tribunal(URL_HMLG), 'tjto.jus.br')
        self.assertEqual(chave_tribunal('https://pje.tjma.jus.br/pje/ws'), 'tjma.jus.br')

    def test_espera_o_intervalo_desde_o_fim_da_requisicao_anterior(self):
        relogio = iter([100.0, 100.5, 101.0])  # fim da 1ª; consulta antes da 2ª; fim da 2ª
        with mock.patch.object(limitador, 'agora', side_effect=lambda: next(relogio)), \
                mock.patch.object(limitador, 'sleep') as dormir:
            with vez_do_tribunal(URL_1G):
                pass
            dormir.assert_not_called()  # primeira requisição: não espera
            with vez_do_tribunal(URL_HMLG):  # outro host, mesmo tribunal
                pass
        dormir.assert_called_once_with(1.5)

    def test_outro_tribunal_nao_espera(self):
        with mock.patch.object(limitador, 'sleep') as dormir:
            with vez_do_tribunal(URL_1G):
                pass
            with vez_do_tribunal('https://pje.tjma.jus.br/pje/ws'):
                pass
        dormir.assert_not_called()

    def test_tribunal_ocupado_nao_espera_alem_do_limite(self):
        cache.add('mni:trava:tjto.jus.br', 'outro-processo', 60)
        with paciencia(0), self.assertRaises(TribunalOcupado), vez_do_tribunal(URL_1G):
            self.fail('não deveria executar a requisição')
        self.assertEqual(cache.get('mni:trava:tjto.jus.br'), 'outro-processo')  # a trava alheia fica

    def test_libera_a_trava_mesmo_com_erro_na_requisicao(self):
        with self.assertRaises(ValueError), vez_do_tribunal(URL_1G):
            raise ValueError
        self.assertIsNone(cache.get('mni:trava:tjto.jus.br'))

    def test_mni_e_wsdl_passam_pelo_limitador(self):
        cache.add('mni:trava:tjto.jus.br', 'outro-processo', 60)
        cliente = mni.ClienteMNI300(ambiente=None, endereco=URL_1G)
        with paciencia(0), mock.patch('urllib.request.urlopen') as urlopen:
            with self.assertRaisesMessage(mni.ErroMNI, 'atendendo outras consultas'):
                cliente.consultar_documentos('123', 'h', '00000000000000000000', ['d1', 'd2'])
            with self.assertRaises(wsdl.ErroWSDL):
                wsdl._baixar(URL_1G + '?wsdl')
        urlopen.assert_not_called()


class SaidaUnicaTests(SimpleTestCase):
    def test_so_judicial_http_chama_o_tribunal(self):
        """Toda requisição HTTP sai por judicial.http.requisitar, que aplica o limitador."""
        chamadores = sorted(
            str(arquivo.relative_to(settings.BASE_DIR))
            for pasta in ('config', 'core', 'judicial', 'ingestao')
            for arquivo in (settings.BASE_DIR / pasta).rglob('*.py')
            if 'tests' not in arquivo.parts and 'urlopen(' in arquivo.read_text(encoding='utf-8')
        )
        self.assertEqual(chamadores, ['judicial/http.py'])
