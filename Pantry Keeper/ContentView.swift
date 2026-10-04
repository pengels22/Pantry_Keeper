import SwiftUI
import UIKit

struct ContentView: View {
    @StateObject private var model = AppModel()

    var body: some View {
        TabView {
            InventoryView()
                .tabItem { Label("Inventory", systemImage: "shippingbox") }
            ScanView()
                .tabItem { Label("Scan", systemImage: "barcode.viewfinder") }
            UnknownProductsView()
                .tabItem { Label("Unknown", systemImage: "questionmark.app") }
            SettingsView()
                .tabItem { Label("Settings", systemImage: "gearshape") }
        }
        .environmentObject(model)
        .task { await model.refresh() }
        .overlay(alignment: .top) {
            if model.isLoading {
                ProgressView()
                    .padding(12)
                    .background(.regularMaterial, in: Capsule())
                    .padding(.top, 8)
            }
        }
        .alert("Pantry Keeper", isPresented: messageBinding) {
            Button("OK", role: .cancel) { model.message = nil }
        } message: {
            Text(model.message ?? "")
        }
        .alert("Something needs attention", isPresented: errorBinding) {
            Button("OK", role: .cancel) { model.errorMessage = nil }
        } message: {
            Text(model.errorMessage ?? "")
        }
    }

    private var messageBinding: Binding<Bool> {
        Binding(get: { model.message != nil }, set: { if !$0 { model.message = nil } })
    }

    private var errorBinding: Binding<Bool> {
        Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })
    }
}

struct InventoryView: View {
    @EnvironmentObject private var model: AppModel
    @State private var searchText = ""

    private var products: [PantryProduct] {
        let all = model.dashboard?.products ?? []
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return [] }
        return all.filter {
            $0.name.localizedCaseInsensitiveContains(query) ||
            ($0.upc ?? "").contains(query) ||
            ($0.brand ?? "").localizedCaseInsensitiveContains(query)
        }
    }

    private var hasSearch: Bool {
        !searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        NavigationStack {
            List {
                if let dashboard = model.dashboard {
                    Section {
                        HStack(spacing: 12) {
                            StatTile(title: "Products", value: "\(dashboard.products.count)", systemImage: "shippingbox")
                            StatTile(title: "Receipts", value: "\(dashboard.receiptCount)", systemImage: "receipt")
                            StatTile(title: "Unknown", value: "\(dashboard.unknownCount)", systemImage: "questionmark")
                        }
                        .listRowInsets(EdgeInsets(top: 8, leading: 16, bottom: 8, trailing: 16))
                    }
                }
                Section(hasSearch ? "Search Results" : "Current Inventory") {
                    if hasSearch {
                        if products.isEmpty {
                            ContentUnavailableView("No Matches", systemImage: "magnifyingglass", description: Text("Try a product name, brand, or UPC."))
                        } else {
                            ForEach(products) { product in
                                InventoryRow(product: product)
                            }
                        }
                    } else {
                        ContentUnavailableView("Search Inventory", systemImage: "magnifyingglass", description: Text("Current inventory is hidden until you search."))
                    }
                }
            }
            .navigationTitle("Pantry Keeper")
            .searchable(text: $searchText, prompt: "Product, brand, or UPC")
            .refreshable { await model.refresh() }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await model.refresh() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                }
            }
        }
    }
}

struct InventoryRow: View {
    @EnvironmentObject private var model: AppModel
    var product: PantryProduct
    @State private var quantityText: String
    @State private var location: String

    init(product: PantryProduct) {
        self.product = product
        _quantityText = State(initialValue: Self.quantityFormatter.string(from: NSNumber(value: product.inventoryQuantity ?? 0)) ?? "0")
        _location = State(initialValue: product.inventoryLocation ?? product.defaultLocation ?? "")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(product.name).font(.headline)
                    Text([product.brand, product.size, product.upc].compactMap { $0 }.joined(separator: " · "))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            HStack {
                TextField("Qty", text: $quantityText)
                    .keyboardType(.decimalPad)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 88)
                TextField("Location", text: $location)
                    .textFieldStyle(.roundedBorder)
                Button {
                    save()
                } label: {
                    Image(systemName: "checkmark")
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(.vertical, 4)
    }

    private func save() {
        let value = Double(quantityText) ?? 0
        Task {
            await model.updateInventory(productID: product.id, quantity: max(0, value), location: location.isEmpty ? nil : location)
        }
    }

    private static let quantityFormatter: NumberFormatter = {
        let formatter = NumberFormatter()
        formatter.maximumFractionDigits = 2
        formatter.minimumFractionDigits = 0
        return formatter
    }()
}

struct ScanView: View {
    @EnvironmentObject private var model: AppModel
    @State private var showingBarcodeScanner = false
    @State private var showingReceiptCamera = false
    @State private var showingPhotoLibrary = false
    @State private var lookupResult: ProductLookupResult?
    @State private var draft = ProductDraft()

    var body: some View {
        NavigationStack {
            List {
                Section("Inventory UPC") {
                    Button {
                        showingBarcodeScanner = true
                    } label: {
                        Label("Scan Product Barcode", systemImage: "barcode.viewfinder")
                    }
                    HStack {
                        TextField("Enter UPC", text: $draft.upc)
                            .keyboardType(.numberPad)
                        Button("Lookup") {
                            lookupUPC(draft.upc)
                        }
                    }
                    if let lookupResult {
                        LookupResultView(result: lookupResult, draft: $draft) {
                            Task {
                                if await model.saveProduct(draft) {
                                    self.lookupResult = nil
                                    self.draft = ProductDraft()
                                }
                            }
                        }
                    }
                }

                Section("Receipt") {
                    Button {
                        showingReceiptCamera = true
                    } label: {
                        Label("Scan Receipt with Camera", systemImage: "camera")
                    }
                    Button {
                        showingPhotoLibrary = true
                    } label: {
                        Label("Upload Receipt Image", systemImage: "photo")
                    }
                }

                if let scan = model.receiptScan {
                    ReceiptReviewView(scan: scan)
                }
            }
            .navigationTitle("Scan")
            .sheet(isPresented: $showingBarcodeScanner) {
                BarcodeScannerView { code in
                    draft.upc = code
                    lookupUPC(code)
                }
            }
            .sheet(isPresented: $showingReceiptCamera) {
                ImagePicker(sourceType: .camera) { image in
                    Task { await model.scanReceiptImage(image, sourceType: "ios_camera") }
                }
                .ignoresSafeArea()
            }
            .sheet(isPresented: $showingPhotoLibrary) {
                ImagePicker(sourceType: .photoLibrary) { image in
                    Task { await model.scanReceiptImage(image, sourceType: "ios_upload") }
                }
                .ignoresSafeArea()
            }
        }
    }

    private func lookupUPC(_ upc: String) {
        Task {
            guard let result = await model.lookup(upc: upc) else { return }
            lookupResult = result
            if let product = result.product {
                model.message = "Known product: \(product.name)"
            } else {
                draft = ProductDraft(upc: result.upc ?? result.normalizedCode ?? upc, suggestion: result.suggestion)
            }
        }
    }
}

struct LookupResultView: View {
    var result: ProductLookupResult
    @Binding var draft: ProductDraft
    var onSave: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let product = result.product {
                Label(product.name, systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            } else {
                Label(result.suggestion == nil ? "Needs identification" : "Suggestion found", systemImage: result.suggestion == nil ? "questionmark.circle" : "sparkle.magnifyingglass")
                    .foregroundStyle(result.suggestion == nil ? .orange : .blue)
                ProductDraftFields(draft: $draft)
                Button("Save Product", action: onSave)
                    .buttonStyle(.borderedProminent)
                    .disabled(!draft.canSave)
            }
        }
    }
}

struct ReceiptReviewView: View {
    @EnvironmentObject private var model: AppModel
    var scan: ReceiptScan
    @State private var selectedProducts: [String: ProductDraft] = [:]

    private var lines: [ReceiptLine] {
        scan.resolution?.items ?? scan.parsed.items
    }

    var body: some View {
        Section("Receipt Review") {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    ReceiptMeta(label: "Date", value: scan.parsed.purchaseDate ?? "Unknown")
                    ReceiptMeta(label: "Store", value: scan.parsed.storeNumber ?? "Unknown")
                    ReceiptMeta(label: "Total", value: money(scan.parsed.total))
                }
                Text("\(lines.count) line(s) scanned")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                ReceiptLineView(line: line, selectedProducts: $selectedProducts)
            }

            Button {
                Task { await model.importReceipt(selectedProducts: selectedProducts) }
            } label: {
                Label("Import Receipt", systemImage: "square.and.arrow.down")
            }
            .buttonStyle(.borderedProminent)
        }
        .onAppear {
            for line in lines {
                let code = line.upc ?? line.normalizedCode ?? line.rawCode ?? ""
                guard !code.isEmpty, selectedProducts[code] == nil else { continue }
                if line.status == "suggested" {
                    selectedProducts[code] = ProductDraft(upc: code, suggestion: line.suggestion ?? line.candidates?.first)
                }
            }
        }
    }
}

struct ReceiptLineView: View {
    var line: ReceiptLine
    @Binding var selectedProducts: [String: ProductDraft]

    private var code: String { line.upc ?? line.normalizedCode ?? line.rawCode ?? "" }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                VStack(alignment: .leading) {
                    Text(line.receiptDescription ?? "Receipt item")
                        .font(.headline)
                    Text([code, "Qty \(line.quantity ?? 1)", money(line.lineTotal)].filter { !$0.isEmpty }.joined(separator: " · "))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                StatusBadge(status: line.status ?? "unresolved")
            }
            if line.status == "resolved", let product = line.product {
                Text(product.name).foregroundStyle(.secondary)
            } else {
                ProductDraftFields(draft: bindingForDraft())
            }
        }
        .padding(.vertical, 4)
    }

    private func bindingForDraft() -> Binding<ProductDraft> {
        Binding {
            selectedProducts[code] ?? ProductDraft(upc: code, suggestion: line.suggestion ?? line.candidates?.first)
        } set: { value in
            selectedProducts[code] = value
        }
    }
}

struct UnknownProductsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationStack {
            List {
                if model.unknownProducts.isEmpty {
                    ContentUnavailableView("No Unknown Products", systemImage: "checkmark.circle", description: Text("Receipt lines that need product names will appear here."))
                } else {
                    ForEach(model.unknownProducts) { row in
                        UnknownProductRow(row: row)
                    }
                }
            }
            .navigationTitle("Unknown")
            .refreshable { await model.refresh() }
        }
    }
}

struct UnknownProductRow: View {
    @EnvironmentObject private var model: AppModel
    var row: UnknownProduct
    @State private var draft: ProductDraft

    init(row: UnknownProduct) {
        self.row = row
        _draft = State(initialValue: ProductDraft(upc: row.upc ?? row.normalizedCode ?? row.rawCode ?? "", suggestion: row.suggestion))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(row.description ?? "Unknown product").font(.headline)
            Text([draft.upc, "Qty \(row.quantity ?? 1)", money(row.lineTotal)].filter { !$0.isEmpty }.joined(separator: " · "))
                .font(.caption)
                .foregroundStyle(.secondary)
            ProductDraftFields(draft: $draft)
            Button {
                Task { _ = await model.resolveUnknown(row, draft: draft) }
            } label: {
                Label("Save Product", systemImage: "checkmark")
            }
            .buttonStyle(.borderedProminent)
            .disabled(!draft.canSave)
        }
        .padding(.vertical, 6)
    }
}

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationStack {
            Form {
                Section("Backend") {
                    TextField("Server URL", text: $model.serverURLString)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    Button {
                        Task { await model.refresh() }
                    } label: {
                        Label("Test Connection", systemImage: "network")
                    }
                }
                Section("About") {
                    Text("This iOS app uses the Pantry Keeper FastAPI backend copied into Backend/Pantry_Keeper. Run that server on your Mac, Raspberry Pi, or home server, then point this app at its URL.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
        }
    }
}

struct ProductDraftFields: View {
    @Binding var draft: ProductDraft

    var body: some View {
        Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 10) {
            GridRow {
                Text("UPC")
                TextField("UPC", text: $draft.upc)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.numberPad)
            }
            GridRow {
                Text("Name")
                TextField("Product name", text: $draft.name)
                    .textFieldStyle(.roundedBorder)
            }
            GridRow {
                Text("Brand")
                TextField("Brand", text: $draft.brand)
                    .textFieldStyle(.roundedBorder)
            }
            GridRow {
                Text("Size")
                TextField("Size", text: $draft.size)
                    .textFieldStyle(.roundedBorder)
            }
            GridRow {
                Text("Category")
                TextField("Category", text: $draft.category)
                    .textFieldStyle(.roundedBorder)
            }
            GridRow {
                Text("Location")
                TextField("Default location", text: $draft.defaultLocation)
                    .textFieldStyle(.roundedBorder)
            }
        }
        .font(.subheadline)
    }
}

struct StatTile: View {
    var title: String
    var value: String
    var systemImage: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Image(systemName: systemImage)
                .foregroundStyle(.blue)
            Text(value)
                .font(.title2.bold())
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8))
    }
}

struct StatusBadge: View {
    var status: String

    var body: some View {
        Text(label)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(color.opacity(0.14), in: Capsule())
            .foregroundStyle(color)
    }

    private var label: String {
        switch status {
        case "resolved": "Known"
        case "suggested": "Suggested"
        default: "Unknown"
        }
    }

    private var color: Color {
        switch status {
        case "resolved": .green
        case "suggested": .blue
        default: .orange
        }
    }
}

struct ReceiptMeta: View {
    var label: String
    var value: String

    var body: some View {
        VStack(alignment: .leading) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.subheadline.weight(.semibold))
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

func money(_ value: Double?) -> String {
    guard let value else { return "" }
    return value.formatted(.currency(code: Locale.current.currency?.identifier ?? "USD"))
}

#Preview {
    ContentView()
}
