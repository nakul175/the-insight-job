class PrivateResponseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Cache-Control"] = "no-store, private"
        response["Pragma"] = "no-cache"
        response["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; img-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        return response
