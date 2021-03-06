# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
"""

Session-storage-related code for the minimal sauropod server.

"""

import os
import time
import hashlib
import hmac
import math
from base64 import urlsafe_b64encode as b64encode
from base64 import urlsafe_b64decode as b64decode

from zope.interface import implements, Interface

from mozsvc import plugin

from pysauropod.utils import strings_differ


def includeme(config):
    """Include the default app-session-management definitions.

    Call this function on a pyramid configurator to register a utility for
    the IAppSessionDB interface.  The particular implementation to use will
    be taken from the configurator settings dict, falling back to a simple
    in-memory implementation as the default.
    """
    settings = config.get_settings()
    if "sauropod.session.backend" not in settings:
        default_backend = "pysauropod.server.session.SignedSessionManager"
        settings["sauropod.session.backend"] = default_backend
    plugin.load_and_register("sauropod.session", config)


class ISessionManager(Interface):
    """Interface for implementing application-session-management."""

    def new_session(appid, userid):
        """Create a new session associated with an appid and userid.

        This method creates a new session and associates with it the given
        appid and userid.  The sessionid is returned.
        """

    def get_session_data(sessionid):
        """Load the appid and userid for to the given sessionid.

        This method retrieves the appid and userid corresponding to the given
        sessionid and returns them as a tuple.
        """


class SignedSessionManager(object):
    """Application-session-management based on signed tokens.

    This class implements the ISessionManager interface using signed session
    tokens.  The appid and userid are incorporated directly into the sessionid
    along with a random token.
    """
    implements(ISessionManager)

    def __init__(self, secret=None, timeout=None):
        if secret is None:
            secret = os.urandom(16)
        if timeout is None:
            timeout = 5 * 60
        self.secret = secret
        self.timeout = timeout
        # Since the secret might be shared with other classes or other
        # servers, generate our own unique keys from it using HKDF.
        # This will help avoid accidentlly becoming e.g. a signature oracle.
        self._master_key = HKDF_extract("ISessionManager", secret)
        self._sig_key = HKDF_expand(self._master_key, "SIGNING", 16)

    def new_session(self, appid, userid):
        """Create a new session associated with an appid and userid.

        In the implementation the session data is actually encoded into the
        sessionid itself, so we don't have to store anything in a database.
        It also encodes timestamp so we can expire old sessions.
        """
        # The sessionid is timestamp:data:signature.
        # The data is b64encode(random:appid:userid).
        # The signature is a hmac using our secret signing key.
        timestamp = hex(int(time.time()))
        # Remove hex-formatting guff e.g. "0x31220ead8L" => "31220ead8"
        timestamp = timestamp[2:]
        if timestamp.endswith("L"):
            timestamp = timestamp[:-1]
        if isinstance(appid, unicode):
            appid = appid.encode("ascii")
        if isinstance(userid, unicode):
            userid = userid.encode("ascii")
        data = b64encode("%s:%s:%s" % (os.urandom(4), appid, userid))
        data = "%s:%s" % (timestamp, data)
        # Append the signature.
        sig = b64encode(hmac.new(self._sig_key, data).digest())
        return "%s:%s" % (data, sig)

    def get_session_data(self, sessionid):
        """Load the appid and userid for the given sessionid.

        In this implementation this involves validating the embedded
        signature, then just extracting the data from the sessionid itself.
        If the sessionid is invalid or expired then None is returned.
        """
        try:
            (timestamp, data, sig) = sessionid.rsplit(":", 2)
        except ValueError:
            return None
        # Check for session expiry.
        try:
            expiry_time = int(timestamp, 16) + self.timeout
        except ValueError:
            return None
        if expiry_time <= time.time():
            return None
        # Check for valid signature.
        sigdata = "%s:%s" % (timestamp, data)
        expected_sig = b64encode(hmac.new(self._sig_key, sigdata).digest())
        if strings_differ(sig, expected_sig):
            return None
        # Hooray!
        _, appid, userid = b64decode(data).split(":", 2)
        userid = userid.decode("utf8")
        return appid, userid


def HKDF_extract(salt, IKM):
    """HKDF-Extract; see RFC-5869 for the details."""
    return hmac.new(salt, IKM, hashlib.sha1).digest()


def HKDF_expand(PRK, info, L):
    """HKDF-Expand; see RFC-5869 for the details."""
    digest_size = hashlib.sha1().digest_size
    N = int(math.ceil(L * 1.0 / digest_size))
    assert N <= 255
    T = ""
    output = []
    for i in xrange(1, N + 1):
        data = T + info + chr(i)
        T = hmac.new(PRK, data, hashlib.sha1).digest()
        output.append(T)
    return "".join(output)[:L]
