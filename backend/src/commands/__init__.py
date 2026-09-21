"""Orquestadores de logica usando el patron ResultSet (poco try/except).

Cada command recibe dependencias y expone ``run()`` que devuelve un
``ResultSet``. El flujo controlado via tipo de retorno, sin excepciones
para errores esperados.

Nomenclatura: ``{name}_command.py``; excepcion ``result_pattern.py`` que define el
patron ResultSet compartido por todos los commands.
"""

from .save_record_command import SaveRecordCommand

__all__ = ["SaveRecordCommand"]
