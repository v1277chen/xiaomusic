'use strict'
var cacheStorageKey = 'xiaomusic-key';
let cacheName = 'xiaomusic-cache'; // 緩存名字

var cacheList = [ // 所需緩存的文件
  '/',
  "index.html"
]

self.addEventListener('install', function (e) {
  console.log('Cache event!')
  e.waitUntil(
    // 安裝服務者時，對需要緩存的文件進行緩存
    caches.open(cacheStorageKey).then(function (cache) {
      console.log('Adding to Cache:', cacheList)
      return cache.addAll(cacheList)
    }).then(function () {
      console.log('Skip waiting!')
      return self.skipWaiting()
    })
  )
})

self.addEventListener('activate', function (e) {
  console.log('Activate event')
  e.waitUntil(
    Promise.all(
      caches.keys().then(cacheNames => {
        return cacheNames.map(name => {
          if (name !== cacheStorageKey) {
            return caches.delete(name)
          }
        })
      })
    ).then(() => {
      console.log('Clients claims.')
      return self.clients.claim()
    })
  )
})

