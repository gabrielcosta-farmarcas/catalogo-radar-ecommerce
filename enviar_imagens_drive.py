"""
Sobe para o Google Drive as fotos que já estão em imagens/ (volume do Coolify: /app/imagens) e
grava o link em produtos.imagem_drive_url. Serve para as fotos geradas antes de o Drive ser
configurado e para repetir envios que falharam (o enriquecimento só avisa, não reprocessa).

Por padrão só envia EANs que ainda não têm imagem_drive_url; --todas reenvia tudo (atualiza o
arquivo existente no Drive, não duplica).

Uso:
    python enviar_imagens_drive.py
    python enviar_imagens_drive.py --todas --limit 50

Requer GOOGLE_SERVICE_ACCOUNT_JSON e DRIVE_FOLDER_ID (ver drive.py).
"""

import argparse
import os
import sys

import drive
import enrich_produtos as ep


def _fotos():
    for medicamento, pasta in ((True, ep.DIRETORIO_IMAGENS_MEDICAMENTOS), (False, ep.DIRETORIO_IMAGENS_NAO_MEDICAMENTOS)):
        if not os.path.isdir(pasta):
            continue
        for nome in sorted(os.listdir(pasta)):
            if nome.lower().endswith(".jpg"):
                yield medicamento, os.path.join(pasta, nome), nome[:-4]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--todas", action="store_true", help="reenvia também as que já têm link")
    parser.add_argument("--limit", type=int, default=None, help="máximo de fotos a enviar")
    args = parser.parse_args()

    if not drive.configurado():
        sys.exit("Drive não configurado: defina GOOGLE_SERVICE_ACCOUNT_JSON e DRIVE_FOLDER_ID.")

    enviadas = puladas = falhas = 0
    conn = ep.conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT ean, imagem_drive_url FROM produtos")
            links = dict(cur.fetchall())
        for medicamento, caminho, ean in _fotos():
            if ean not in links:
                print(f"[pula] {ean}: não existe em produtos")
                puladas += 1
                continue
            if links[ean] and not args.todas:
                puladas += 1
                continue
            if args.limit is not None and enviadas >= args.limit:
                break
            if ep.enviar_imagem_drive(conn, ean, caminho, medicamento):
                enviadas += 1
            else:
                falhas += 1
    finally:
        conn.close()
    print(f"Enviadas: {enviadas} | puladas: {puladas} | falhas: {falhas}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
