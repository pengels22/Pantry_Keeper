import Foundation
import UIKit

enum PantryAPIError: LocalizedError {
    case invalidServerURL
    case badResponse
    case server(String)

    var errorDescription: String? {
        switch self {
        case .invalidServerURL:
            return "Enter a valid Pantry Keeper server URL."
        case .badResponse:
            return "The server returned an unexpected response."
        case .server(let message):
            return message
        }
    }
}

struct PantryAPI {
    var baseURL: URL

    func dashboard() async throws -> DashboardResponse {
        try await request("/api/dashboard")
    }

    func unknownProducts() async throws -> [UnknownProduct] {
        try await request("/api/unknown-products")
    }

    func updateInventory(productID: Int, quantity: Double, location: String?) async throws {
        let payload: [String: Any?] = ["quantity": quantity, "location": location]
        let _: EmptyResponse = try await request(
            "/api/inventory/\(productID)",
            method: "POST",
            body: try JSONSerialization.data(withJSONObject: payload.compactMapValues { $0 }),
            contentType: "application/json"
        )
    }

    func lookupProduct(upc: String) async throws -> ProductLookupResult {
        var components = URLComponents(url: baseURL.appendingPathComponent("/api/products/lookup"), resolvingAgainstBaseURL: false)
        components?.queryItems = [URLQueryItem(name: "upc", value: upc)]
        guard let url = components?.url else { throw PantryAPIError.invalidServerURL }
        return try await request(url)
    }

    func createProduct(_ draft: ProductDraft) async throws -> PantryProduct {
        try await request(
            "/api/products",
            method: "POST",
            body: try JSONSerialization.data(withJSONObject: draft.payload.compactMapValues { $0 }),
            contentType: "application/json"
        )
    }

    func resolveUnknown(receiptItemID: Int, draft: ProductDraft) async throws -> PantryProduct {
        struct ResolveResponse: Codable { var product: PantryProduct }
        let response: ResolveResponse = try await request(
            "/api/unknown-products/\(receiptItemID)/resolve",
            method: "POST",
            body: try JSONSerialization.data(withJSONObject: draft.payload.compactMapValues { $0 }),
            contentType: "application/json"
        )
        return response.product
    }

    func scanReceiptImage(_ image: UIImage, sourceType: String) async throws -> ReceiptScan {
        guard let imageData = image.jpegData(compressionQuality: 0.82) else {
            throw PantryAPIError.server("Could not prepare the receipt image.")
        }
        let boundary = "Boundary-\(UUID().uuidString)"
        var body = Data()
        body.appendMultipartField(name: "source_type", value: sourceType, boundary: boundary)
        body.appendMultipartFile(name: "file", filename: "receipt.jpg", mimeType: "image/jpeg", data: imageData, boundary: boundary)
        body.append("--\(boundary)--\r\n".data(using: .utf8)!)
        return try await request(
            "/api/receipts/scan-image",
            method: "POST",
            body: body,
            contentType: "multipart/form-data; boundary=\(boundary)"
        )
    }

    func resolvePreview(scan: ReceiptScan) async throws -> ReceiptResolution {
        let payload = ["parsed": try scan.parsed.jsonObject()]
        let body = try JSONSerialization.data(withJSONObject: payload)
        return try await request("/api/receipts/resolve-preview", method: "POST", body: body, contentType: "application/json")
    }

    func importReceipt(scan: ReceiptScan, selectedProducts: [String: ProductDraft]) async throws -> ImportResult {
        var selected: [String: Any] = [:]
        for (code, draft) in selectedProducts where draft.canSave {
            selected[code] = draft.payload.compactMapValues { $0 }
        }
        let payload: [String: Any?] = [
            "source_type": scan.sourceType,
            "source_format": scan.sourceFormat,
            "raw_text": scan.rawText,
            "ocr_text": scan.ocrText,
            "parsed": try scan.parsed.jsonObject(),
            "selected_products": selected,
        ]
        return try await request(
            "/api/receipts/import",
            method: "POST",
            body: try JSONSerialization.data(withJSONObject: payload.compactMapValues { $0 }),
            contentType: "application/json"
        )
    }

    func request<T: Decodable>(_ path: String, method: String = "GET", body: Data? = nil, contentType: String? = nil, timeout: TimeInterval = 90) async throws -> T {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false),
              let relative = URLComponents(string: path) else { throw PantryAPIError.invalidServerURL }
        let prefix = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        components.path = prefix.isEmpty ? relative.path : "/" + prefix + relative.path
        components.queryItems = relative.queryItems
        guard let url = components.url else { throw PantryAPIError.invalidServerURL }
        return try await request(url, method: method, body: body, contentType: contentType, timeout: timeout)
    }

    private func request<T: Decodable>(_ url: URL, method: String = "GET", body: Data? = nil, contentType: String? = nil, timeout: TimeInterval = 90) async throws -> T {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.httpBody = body
        request.timeoutInterval = timeout
        if let contentType {
            request.setValue(contentType, forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw PantryAPIError.badResponse }
        guard (200..<300).contains(http.statusCode) else {
            if http.statusCode == 404, url.path.contains("recipes") || url.path == "/api/inventory" {
                throw PantryAPIError.server("This server needs the updated Recipe Assistant backend. Check the Server URL in Settings.")
            }
            if let error = try? JSONDecoder().decode(ServerError.self, from: data) {
                throw PantryAPIError.server(error.detail)
            }
            if http.statusCode == 422 {
                throw PantryAPIError.server("The server could not accept these values. Check ingredient quantities and units.")
            }
            throw PantryAPIError.server("Request failed with status \(http.statusCode).")
        }
        if T.self == EmptyResponse.self {
            return EmptyResponse() as! T
        }
        let decoder = JSONDecoder()
        return try decoder.decode(T.self, from: data)
    }
}

private struct EmptyResponse: Codable {}

private struct ServerError: Codable {
    var detail: String
}

private extension Encodable {
    func jsonObject() throws -> Any {
        let data = try JSONEncoder().encode(self)
        return try JSONSerialization.jsonObject(with: data)
    }
}

private extension Data {
    mutating func appendMultipartField(name: String, value: String, boundary: String) {
        append("--\(boundary)\r\n".data(using: .utf8)!)
        append("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n".data(using: .utf8)!)
        append("\(value)\r\n".data(using: .utf8)!)
    }

    mutating func appendMultipartFile(name: String, filename: String, mimeType: String, data: Data, boundary: String) {
        append("--\(boundary)\r\n".data(using: .utf8)!)
        append("Content-Disposition: form-data; name=\"\(name)\"; filename=\"\(filename)\"\r\n".data(using: .utf8)!)
        append("Content-Type: \(mimeType)\r\n\r\n".data(using: .utf8)!)
        append(data)
        append("\r\n".data(using: .utf8)!)
    }
}
