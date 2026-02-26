"""Super minimal test - no FastAPI, just basic HTTP server"""
import http.server
import socketserver

PORT = 8000

class MyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"Hello from Databricks Apps!")

with socketserver.TCPServer(("", PORT), MyHandler) as httpd:
    print(f"Server running on port {PORT}")
    httpd.serve_forever()
