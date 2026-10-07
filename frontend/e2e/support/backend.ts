/**
 * Where the backend lives and how the end-to-end run empties its database.
 * Used by playwright.config.ts (before the API starts) and by the global
 * teardown (after the last test).
 */
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { E2E_DATABASE, MONGODB_URI } from './env.ts'

const supportDir = path.dirname(fileURLToPath(import.meta.url))

export const FRONTEND_DIR = path.resolve(supportDir, '../..')
export const BACKEND_DIR = path.resolve(FRONTEND_DIR, '../backend')
/** The interpreter of the backend's virtual environment, which has pymongo and uvicorn. */
export const BACKEND_PYTHON = path.join(BACKEND_DIR, '.venv', 'bin', 'python')

/** A Python one-liner that drops the throwaway database (and nothing else). */
export const DROP_E2E_DATABASE = `from pymongo import MongoClient; MongoClient('${MONGODB_URI}', serverSelectionTimeoutMS=5000).drop_database('${E2E_DATABASE}')`
