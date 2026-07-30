package com.gimp.backend.exception;

import java.util.Map;

public record ErrorResponse(String message, Map<String, String> fieldErrors) {

    public static ErrorResponse of(String message) {
        return new ErrorResponse(message, null);
    }

    public static ErrorResponse of(String message, Map<String, String> fieldErrors) {
        return new ErrorResponse(message, fieldErrors);
    }
}
