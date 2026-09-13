import { defineConfig } from 'eslint/config';
import globals from 'globals';
import js from '@eslint/js';
import { includeIgnoreFile } from '@eslint/compat';
import { fileURLToPath } from 'node:url';

const gitignorePath = fileURLToPath(new URL('.gitignore', import.meta.url));

export default defineConfig([
  includeIgnoreFile(gitignorePath, 'imported .gitignore patterns'),
  {
    files: ['**/*.js'],
    languageOptions: { globals: globals.node },
    plugins: { js },
    extends: ['js/recommended'],
  },
]);
