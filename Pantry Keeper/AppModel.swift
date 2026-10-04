import Combine
import Foundation
import UIKit

@MainActor
final class AppModel: ObservableObject {
    @Published var serverURLString: String {
        didSet { UserDefaults.standard.set(serverURLString, forKey: "serverURLString") }
    }
    @Published var dashboard: DashboardResponse?
    @Published var unknownProducts: [UnknownProduct] = []
    @Published var receiptScan: ReceiptScan?
    @Published var isLoading = false
    @Published var message: String?
    @Published var errorMessage: String?

    init() {
        serverURLString = UserDefaults.standard.string(forKey: "serverURLString") ?? "http://localhost:8000"
    }

    var api: PantryAPI? {
        guard let url = URL(string: serverURLString.trimmingCharacters(in: .whitespacesAndNewlines)) else { return nil }
        return PantryAPI(baseURL: url)
    }

    func refresh() async {
        await run("Refreshed Pantry Keeper.") { api in
            async let dashboard = api.dashboard()
            async let unknown = api.unknownProducts()
            self.dashboard = try await dashboard
            self.unknownProducts = try await unknown
        }
    }

    func updateInventory(productID: Int, quantity: Double, location: String?) async {
        await run("Inventory updated.") { api in
            try await api.updateInventory(productID: productID, quantity: quantity, location: location)
            self.dashboard = try await api.dashboard()
        }
    }

    func saveProduct(_ draft: ProductDraft) async -> Bool {
        var saved = false
        await run("Product saved.") { api in
            _ = try await api.createProduct(draft)
            self.dashboard = try await api.dashboard()
            self.unknownProducts = try await api.unknownProducts()
            saved = true
        }
        return saved
    }

    func resolveUnknown(_ row: UnknownProduct, draft: ProductDraft) async -> Bool {
        var saved = false
        await run("Unknown product resolved.") { api in
            _ = try await api.resolveUnknown(receiptItemID: row.receiptItemID, draft: draft)
            self.dashboard = try await api.dashboard()
            self.unknownProducts = try await api.unknownProducts()
            saved = true
        }
        return saved
    }

    func lookup(upc: String) async -> ProductLookupResult? {
        guard let api else {
            errorMessage = PantryAPIError.invalidServerURL.localizedDescription
            return nil
        }
        do {
            return try await api.lookupProduct(upc: upc)
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    func scanReceiptImage(_ image: UIImage, sourceType: String) async {
        await run("Receipt scanned. Review it before importing.") { api in
            var scan = try await api.scanReceiptImage(image, sourceType: sourceType)
            scan.resolution = try await api.resolvePreview(scan: scan)
            self.receiptScan = scan
        }
    }

    func importReceipt(selectedProducts: [String: ProductDraft]) async {
        guard let scan = receiptScan else { return }
        await run(nil) { api in
            let result = try await api.importReceipt(scan: scan, selectedProducts: selectedProducts)
            self.receiptScan = nil
            self.dashboard = try await api.dashboard()
            self.unknownProducts = try await api.unknownProducts()
            self.message = "Receipt #\(result.receiptID) imported: \(result.resolvedItems) line(s) added, \(result.unresolvedItems) unknown."
        }
    }

    private func run(_ success: String?, action: (PantryAPI) async throws -> Void) async {
        guard let api else {
            errorMessage = PantryAPIError.invalidServerURL.localizedDescription
            return
        }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            try await action(api)
            if let success {
                message = success
            }
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
