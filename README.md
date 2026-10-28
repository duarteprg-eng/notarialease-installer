# NotarialEase Docker Ubuntu — canal de teste 1.0.29

Distribuição para Ubuntu Server amd64, PostgreSQL 18.6 e acesso interno/VPN.
O pacote cria uma instalação nova com catálogos obrigatórios e o administrador
inicial adriano. Atualizações preservam a senha e a configuração de MFA atuais.
Dados de clientes, atendimentos e serventia não fazem parte da imagem.

## Instalação e atualização da instalação existente

A entrada pública verifica o mecanismo Python por SHA-256, a release privada
e o digest da imagem. O Python prepara Docker/Compose se estiverem ausentes,
instala o aplicativo e configura o controlador de manutenção do host.

~~~bash
set -o pipefail
curl -fsSL https://raw.githubusercontent.com/duarteprg-eng/notarialease-installer/docker-bootstrap-v1.0.29-test.1/install.py | sudo python3 - --bind 127.0.0.1 --port 3080
~~~

Use em --bind o IPv4 autorizado da VPN. A porta padrão é 3080. Os parâmetros
--root e --project precisam identificar a instalação existente ao atualizá-la.
Os arquivos privados github-release.token e ghcr-read.token são reutilizados
em /root/.config/notarialease-install. Se não existirem, a entrada solicita os
tokens por terminal protegido. Nenhum token é incluído no script público.
--check-access verifica somente a autorização dos downloads.

Em instalação existente com receipt READY íntegro, o instalador configura o
controlador e solicita uma atualização com UUID persistido, sem executar setup
inicial. Uma falha após começar a alteração do banco exige revisão; repetir o
comando não força nova execução nem restaura SQL automaticamente.

## Admin: Atualização do Sistema

Exige simultaneamente usuarios_gerenciar e sistema_configurar, sessão válida,
origem autorizada, senha atual e auditoria persistida antes de iniciar.
O controlador baixa uma release permitida do GitHub e verifica os hashes e o
digest GHCR. Pausa o aplicativo, cria backups dos dois bancos e das roles,
testa a restauração e as migrações em PostgreSQL isolado, e somente depois
atualiza o banco em uso e inicia a imagem candidata. A senha, os vínculos e os
fatores de autenticação são conferidos antes e depois das migrações.

O aplicativo usa um socket Unix restrito; não recebe Docker socket, tokens
GitHub ou senha do superusuário PostgreSQL. Durante manutenção, as mutações
HTTP ficam bloqueadas. Atualização sem confirmação permanece desativada neste
canal de teste. A interface acompanha o andamento e reconecta após reinício.

## Admin: Restauração de Dados

Use o par do mesmo lote: controle_senhas.nexo.jsonl.gz e
atendimento_fluxo.nexo.jsonl.gz. O formato NEXO migration v1 permanece o mesmo;
os arquivos já gerados não precisam de conversão. O gerador está em
tools/notarialease-migration/nexo_migration.py e na habilidade privada
nexo123-notarialease-migracao do plugin NEXO123.

Cada nova análise usa explicitamente restore_source, com novo job e plano
vinculado a SHA. Prévia do modo anterior serve apenas ao histórico; não pode
ser aplicada como restauração. O modo anterior que criava atores históricos
inativos foi substituído.

A restauração carrega os dados operacionais a partir de todas as tabelas e
colunas compatíveis da origem, preservando IDs, nomes de login, estados,
credenciais, perfis, permissões, competências e configurações. Registros
inativos na origem permanecem inativos. Usuários legados sem autenticação web
utilizam o mecanismo oficial de primeiro login, com a senha original.

Exceções explícitas: o administrador atual mantém sua autenticação, MFA e
acesso administrativo. O controle técnico de versão do esquema atual é mantido
(app_database_migration_log e app_schema_version em cada banco), evitando
reaplicar migrações históricas sobre os dados restaurados. Os registros técnicos
de origem também ficam preservados e conferidos na cópia de origem. O relatório
distingue a quantidade total nos arquivos da quantidade operacional restaurada.
Conflitos de identidade ou
material criptográfico necessário ausente interrompem a análise, sem inventar
usuários, alterar logins ou desabilitar MFA de outras contas.

A operação prepara uma cópia isolada, preserva os bancos atuais e restaura os
registros históricos sem inventar eventos. Durante a carga somente no clone,
os triggers de usuário têm seus estados guardados e restaurados antes da
validação; chaves estrangeiras e comparações de dados continuam obrigatórias.
O controlador exige prova de fidelidade ligada ao plano e repete a comparação
após as migrações e o teste do aplicativo, antes de promover o novo volume.
O volume original e os backups ficam preservados.

Confira a prévia e confirme RESTAURAR DADOS com a senha atual. Os dados dos dois
bancos são substituídos pelos dados dos arquivos; alterações posteriores ao
backup não fazem parte da restauração. A aplicação exige confirmação nova;
não repete automaticamente um pedido de aplicação perdido.

Os dois arquivos privados podem ter até 200 MiB por banco. Gzip não cifra dados.
Não publique os arquivos, dumps, credenciais ou dados reais em GitHub ou imagem.
Anexos externos e chaves ausentes dos bancos precisam de material próprio.

## Limites e recuperação

Falha ou interrupção depois de iniciar migração em uso produz RECOVERY_REQUIRED,
bloqueia novas alterações e mantém o material de recuperação para suporte.
Não há exclusão automática do volume original. Os backups completos podem
conter credenciais e dados pessoais e ficam em diretório root privado.
Anexos externos que não estejam nos bancos precisam ser transferidos por
procedimento próprio; arquivos e chaves ausentes não são recriados.

A instalação por curl e a aplicação de migração exigem validação da release
publicada e aceite no ambiente de destino; testes locais não substituem isso.
