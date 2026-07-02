import { register } from 'node:module'

register(new URL('./node-test-alias-loader.mjs', import.meta.url))
