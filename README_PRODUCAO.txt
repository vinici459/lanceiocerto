Lancei o Certo - pacote de produção seguro

Alterações principais:
- Home profissional com apresentação animada dentro do card principal.
- Cadastro com campos obrigatórios, documento e selfie.
- Conta fica pendente até verificação do administrador.
- Usuário pode participar de leilões com documentos pendentes.
- Saques e pagamento/envio de produto ficam bloqueados até conta verificada.
- Depósitos ficam registrados como pendentes; saldo real deve ser creditado apenas por webhook do gateway.
- Lances não debitam saldo. Dinheiro real só deve circular em depósito confirmado, pagamento de pedido vencedor e saque aprovado.

Depois de substituir os arquivos:
git add .
git commit -m "ajustes profissionais de producao"
git push
