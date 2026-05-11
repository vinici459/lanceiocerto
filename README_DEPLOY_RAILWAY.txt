PASSOS PARA SUBIR NO RAILWAY

1. Suba estes arquivos para um repositório GitHub.
2. No Railway: New Project > Deploy from GitHub Repo.
3. Adicione um banco: New > Database > PostgreSQL.
4. No serviço web, confira se a variável DATABASE_URL foi criada.
5. O Railway usará o Procfile:
   web: uvicorn app:app --host 0.0.0.0 --port $PORT
6. Após o deploy abrir no domínio .up.railway.app, configure seu domínio próprio.

IMPORTANTE
- O app já lê DATABASE_URL do ambiente.
- Se DATABASE_URL não existir, ele roda localmente com SQLite.
- O cadastro agora tem apelido público. Esse apelido aparece como @apelido nos lances e vencedores.
- Para produção real com documentos/selfies, o ideal é migrar uploads para Cloudinary/Supabase Storage/S3.
