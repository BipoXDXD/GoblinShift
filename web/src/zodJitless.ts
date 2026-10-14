// A CSP da SPA (docs/GoblinShiftPlan.md, 3.6) não libera eval: o Zod valida sem compilar schemas
// com new Function. Módulo só de efeito, importado primeiro pelo main.tsx: o Zod testa o eval ao
// criar cada schema, e os schemas nascem quando os módulos que os declaram são avaliados.
import { z } from "zod";

z.config({ jitless: true });
