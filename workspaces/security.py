def direct_client_ip(request):
    # Do not trust client-supplied forwarding headers.
    return request.META.get("REMOTE_ADDR")
