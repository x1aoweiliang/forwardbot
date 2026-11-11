import Cookies from 'js-cookie'

const TokenKey = import.meta.env.VITE_AUTH_TOKEN_KEY || 'Admin-Token'

export function getToken() {
  return Cookies.get(TokenKey)
}

export function setToken(token) {
  return Cookies.set(TokenKey, token)
}

export function removeToken() {
  return Cookies.remove(TokenKey)
}
