# NotarialEase — instalação Ubuntu por curl

Este repositório publica somente a entrada e o mecanismo Python do instalador. Aplicação, imagens e releases permanecem privadas. Não há tokens, bancos ou dados de clientes neste repositório.

## Instalar ou atualizar

No Ubuntu Server amd64 com curl, Python 3 e sudo:

~~~bash
set -o pipefail
curl -fsSL https://raw.githubusercontent.com/duarteprg-eng/notarialease-installer/docker-bootstrap-v1.0.28-test.2/install.py | sudo python3 - --bind 127.0.0.1 --port 3080
~~~

Troque o IPv4 pelo endereço autorizado da VPN. A porta padrão é 3080. Ao atualizar, use o mesmo --root e --project da instalação existente. O script baixa o mecanismo engine.py deste mesmo tag e verifica seu SHA-256 e os metadados da release privada. Os tags anteriores permanecem disponíveis.

Os dois acessos de leitura são mantidos no servidor em /root/.config/notarialease-install: github-release.token e ghcr-read.token, arquivos root0600/0400 em pasta root0700. Se ausentes, a entrada solicita os tokens em terminal protegido, valida o acesso e os salva. Nenhum token é incorporado ao script, à URL ou aos argumentos.

O primeiro token precisa de Contents/Metadata de leitura somente no repositório notarialease. O segundo precisa somente read:packages no GHCR. O token de pacotes pode alcançar os pacotes acessíveis à conta emissora; não permite upload ou exclusão. A preparação é individual por servidor.

Para conferir apenas acesso:

~~~bash
set -o pipefail
curl -fsSL https://raw.githubusercontent.com/duarteprg-eng/notarialease-installer/docker-bootstrap-v1.0.28-test.2/install.py | sudo python3 - --check-access
~~~

Esse modo não executa Docker, APT, setup ou banco. No modo de instalação, Docker/Compose ausentes são preparados pela fonte oficial. O instalador cria a aplicação e PostgreSQL 18.6 com os catálogos obrigatórios, sem atendimentos nem serventia. O administrador inicial precisa trocar a senha no primeiro acesso.

## Manutenção pelo Admin

A versão 1.0.28 oferece Atualização do Sistema e Migração de Dados no Admin. A atualização baixa uma release permitida do GitHub, valida imagem/backups/migrações e preserva credenciais e MFA existentes. Cada aplicação exige confirmação do administrador; atualização sem confirmação permanece desabilitada no canal de teste.

A migração recebe um arquivo NEXO migration v1 (.nexo.jsonl.gz) para cada um dos dois bancos, sempre do mesmo lote. Gera prévia, preserva as tabelas originais e prepara os dados em PostgreSQL isolado antes de solicitar confirmação. Arquivos reais devem permanecer privados; gzip não cifra dados. Campos antigos sem equivalente e históricos incompatíveis ficam preservados nas tabelas legadas e podem não aparecer na UI atual. Usuários históricos precisam de revisão para ativar acesso.

Um controlador restrito do host executa as operações por socket Unix. O aplicativo não recebe Docker socket nem os tokens GitHub. Falha depois de iniciar migração em uso bloqueia novas alterações e exige revisão; não há restauração SQL automática. O volume original é preservado na promoção de dados.
