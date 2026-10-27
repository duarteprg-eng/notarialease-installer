# NotarialEase — instalador Ubuntu

Este repositorio publica somente o instalador Python generico. A aplicacao, imagens e releases de instalacao ficam privadas. Nenhum banco, senha, hash inicial de usuario ou dado de cliente e publicado aqui.

Canal atual: teste. Atualizacao automatica desabilitada.

No Ubuntu Server amd64 com curl, Python3 e sudo:

```bash
set -o pipefail
curl -fsSL https://raw.githubusercontent.com/duarteprg-eng/notarialease-installer/docker-v1.0.27-test.1/install.py | sudo python3 -
```

Use --bind IP_DA_VPN --port 3080 para disponibilizar na VPN. O instalador solicita token de leitura do GitHub/GHCR em campo protegido; nao coloque token na URL ou no comando. Se precisar: --github-user USUARIO. Tokens do registry sao guardados apenas no servidor em arquivo root0600.

Docker/Compose ausentes sao instalados pelo repositorio oficial Docker. Instalacoes existentes funcionais sao reutilizadas. A instalacao cria bancos novos, sem atendimentos ou serventia, e habilita Docker para iniciar no boot.

O primeiro login exige troca de senha. Projetos/volumes existentes conflitantes sao preservados e a instalacao para para diagnostico.
