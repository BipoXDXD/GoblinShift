"""Receita image-forge (docs/GoblinShiftPlan.md, 1.2), implementada pela frente B da fatia 5.

Este `__init__` não pode importar o PIL nem módulos que o importam: o catálogo, na API, importa
os parâmetros daqui (contrato "Só o executor carrega o PIL" do import-linter).
"""
