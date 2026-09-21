# Crear Monolito
- descripcion: necesito que tomando solo de ejemplo a my-org-bbit-release-manager
## Backend:
- Python 3.14
- FastApi
- Arquitectura hexagonal: estructura de carpetas
  - /adapter: consultas a servicios externos
    - services: consultas a servicios externos.
    - repositories: aqui ponemos todos los queries o llamados a sp. prefiero queryNativo.
    - entities: entidades de base de datos, no confundir con dtos. son las clases que mappean la info de DB a python
  - /commands: orquestadores de logica, usemos patron ResultSet. muy poco tryCatch
  - /logics: nuestra logica de negocio, mas que nada es el procesamiento de la info, el llamado a repositories, etc
  - /enums: tiene aquellas lista clave vaslor que tenemos en DB como:
    - lista de servicios externos que consultamos
    - lista de ambientes de aws
  - /controllers: contiene los endpoints que le disponibilizamos a front
  - /models: DTOS modelos de las clases que serializan las respuestas para el front
  - handler.py
- el backend debe manejar multihilos, paralelismo y concurrencia.
  - deben ser parametrizables.
- Sqlite: para persistencia de datos

